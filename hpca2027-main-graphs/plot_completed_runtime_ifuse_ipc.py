#!/usr/bin/env python3
"""Plot IPC speedup for completed baseline vs runtime I-Fuse (optional ideal fusion).

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_completed_runtime_ifuse_ipc.py \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/completed_runtime_ifuse_ipc

cd /users/deepmish/scarab
git add src/hpca2027-main-graphs-results/completed_runtime_ifuse_ipc/
git commit -m "Update HPCA main-graph completed runtime I-Fuse IPC results."
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
INFRA_DIR = GRAPH_DIR.parent
SCRIPTS_DIR = INFRA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import DEFAULT_COMPLETED_RUNTIME_IFUSE_IPC_OUTPUT_DIR  # noqa: E402
from plot_pgo_ifuse_results import (  # noqa: E402
    _ylim_speedup_pct_auto,
    _ylim_with_bar_label_headroom,
)

APPS = [
    "appworld",
    "bc",
    "bfs",
    "bfs_gnutella31",
    "clickhouse",
    "community",
    "connected_components",
    "corebench",
    "dfs",
    "dfs_gnutella31",
    "duckdb",
    "leveldb",
    "masstree",
    "memcached",
    "mlgym_fmnist",
    "mysql",
    "pagerank",
    "pagerank-gnutella31",
    "postgres",
    "protobuf",
    "redis",
    "rocksdb",
    "silo",
    "sssp",
    "sssp_ego_fb",
    "sssp_facebook",
    "terminal_bench",
    "triangle_counting",
    "zstd",
]
DISPLAY_NAMES = {
    "appworld": "AppWorld",
    "bc": "BC",
    "bfs": "BFS",
    "bfs_gnutella31": "BFS-Gnu",
    "clickhouse": "ClickHouse",
    "community": "Community",
    "connected_components": "CC",
    "corebench": "CoreBench",
    "dfs": "DFS",
    "dfs_gnutella31": "DFS-Gnu",
    "duckdb": "DuckDB",
    "leveldb": "LevelDB",
    "masstree": "Masstree",
    "memcached": "Memcached",
    "mlgym_fmnist": "MLGym",
    "mysql": "MySQL",
    "pagerank": "PR",
    "pagerank-gnutella31": "PR-Gnu",
    "postgres": "Postgres",
    "protobuf": "Protobuf",
    "redis": "Redis",
    "rocksdb": "RocksDB",
    "silo": "Silo",
    "sssp": "SSSP",
    "sssp_ego_fb": "SSSP-ego",
    "sssp_facebook": "SSSP-FB",
    "terminal_bench": "TermBench",
    "triangle_counting": "TC",
    "zstd": "Zstd",
}
IFUSE_COLOR = "#009900"
IDEAL_FUSION_COLOR = "#000000"
MAROON_COLOR = "#060771"
ARROW_THRESHOLD = 0.54


def read_ipc(path: Path) -> float | None:
    if not path.is_file():
        return None
    instructions = None
    cycles = None
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 3:
                continue
            if row[0].strip() == "Periodic_Instructions":
                instructions = float(row[2])
            elif row[0].strip() == "Periodic_Cycles":
                cycles = float(row[2])
    if instructions is None or cycles is None or cycles <= 0:
        return None
    return instructions / cycles


def weighted_ipc(values: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights[cid] for cid in values)
    return sum(values[cid] * weights[cid] for cid in values) / total


def ipc_path(experiment_dir: Path, config: str, app: str, cid: str) -> Path:
    if config == "ideal_fusion":
        # Flat ideal-fusion layout: {dir}/{app}/{cid}/
        flat = experiment_dir / app / cid / "core.stat.0.csv"
        if flat.is_file():
            return flat
        nested = (
            experiment_dir
            / "ideal-fusion"
            / "datacenter/datacenter"
            / app
            / cid
            / "core.stat.0.csv"
        )
        return nested
    # Prefer nested suite layout; also accept flattened {config}/{app}/{cid}/.
    nested = (
        experiment_dir
        / config
        / "datacenter/datacenter"
        / app
        / cid
        / "core.stat.0.csv"
    )
    if nested.is_file():
        return nested
    return experiment_dir / config / app / cid / "core.stat.0.csv"


def discover_apps_with_stats(experiment_dir: Path, config: str) -> list[str]:
    """Apps under {experiment}/{config}/... that have at least one core.stat.0.csv."""
    roots = [
        experiment_dir / config / "datacenter" / "datacenter",
        experiment_dir / config,
    ]
    found: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for stat in root.glob("*/**/core.stat.0.csv"):
            # .../{app}/{cid}/core.stat.0.csv
            app = stat.parent.parent.name
            if app in ("datacenter", config):
                continue
            found.add(app)
        for stat in root.glob("*/core.stat.0.csv"):
            # unlikely flat; ignore
            pass
        for app_dir in root.iterdir():
            if not app_dir.is_dir() or app_dir.name in ("datacenter",):
                continue
            if any(app_dir.glob("*/core.stat.0.csv")):
                found.add(app_dir.name)
    return sorted(found)


def discover_simpoint_ids(experiment_dir: Path, config: str, app: str) -> list[str]:
    cids: list[str] = []
    for base in (
        experiment_dir / config / "datacenter" / "datacenter" / app,
        experiment_dir / config / app,
    ):
        if not base.is_dir():
            continue
        for sp_dir in sorted(base.iterdir(), key=lambda p: p.name):
            if sp_dir.is_dir() and (sp_dir / "core.stat.0.csv").is_file():
                cids.append(sp_dir.name)
    return cids


def collect_app_ipc(
    baseline_dir: Path,
    runtime_dir: Path,
    ideal_dir: Path | None,
    app: str,
    weights: dict[str, float],
    require_ideal: bool,
    allow_partial: bool = False,
) -> tuple[dict[str, float], dict[str, float], dict[str, float] | None, dict[str, float]] | None:
    baseline: dict[str, float] = {}
    runtime: dict[str, float] = {}
    ideal: dict[str, float] = {}
    for cid in weights:
        base_ipc = read_ipc(ipc_path(baseline_dir, "baseline", app, cid))
        runtime_ipc = read_ipc(ipc_path(runtime_dir, "runtime_ifuse", app, cid))
        if base_ipc is not None:
            baseline[cid] = base_ipc
        if runtime_ipc is not None:
            runtime[cid] = runtime_ipc
        if ideal_dir is not None:
            ideal_ipc = read_ipc(ipc_path(ideal_dir, "ideal_fusion", app, cid))
            if ideal_ipc is not None:
                ideal[cid] = ideal_ipc

    expected = set(weights)
    if allow_partial:
        common = set(baseline) & set(runtime)
        if require_ideal:
            common &= set(ideal)
        if not common:
            return None
        used_weights = {cid: weights[cid] for cid in common}
        return (
            {cid: baseline[cid] for cid in common},
            {cid: runtime[cid] for cid in common},
            ({cid: ideal[cid] for cid in common} if set(ideal) >= common else None),
            used_weights,
        )

    if set(baseline) != expected or set(runtime) != expected:
        return None
    if require_ideal and set(ideal) != expected:
        return None
    return baseline, runtime, (ideal if set(ideal) == expected else None), weights


def plot_speedup_bars(
    workloads: list[str],
    runtime_speedups: list[float],
    ideal_speedups: list[float] | None,
    output_stem: Path,
) -> None:
    import matplotlib.pyplot as plt

    runtime_avg = sum(runtime_speedups) / len(runtime_speedups)
    runtime_pct = runtime_speedups + [runtime_avg]
    has_ideal = ideal_speedups is not None
    if has_ideal:
        ideal_avg = sum(ideal_speedups) / len(ideal_speedups)
        ideal_pct = ideal_speedups + [ideal_avg]
    else:
        ideal_pct = None

    labels = [DISPLAY_NAMES.get(app, app) for app in workloads] + ["Average"]
    x = list(range(len(labels)))
    width = 0.18 if has_ideal else 0.35

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ifuse_edges = ["red" if val < 0 else "black" for val in runtime_pct]
    ifuse_widths = [2.5 if val < 0 else 1.0 for val in runtime_pct]
    ifuse_x = [i - 0.5 * width for i in x] if has_ideal else x
    ax.bar(
        ifuse_x,
        runtime_pct,
        width,
        label="Runtime I-Fuse",
        color=IFUSE_COLOR,
        edgecolor=ifuse_edges,
        linewidth=ifuse_widths,
        zorder=3,
    )
    if has_ideal:
        ax.bar(
            [i + 0.5 * width for i in x],
            ideal_pct,
            width,
            label="Ideal fusion",
            color=IDEAL_FUSION_COLOR,
            edgecolor="black",
            linewidth=1.0,
            zorder=3,
        )

    offsets = [-0.5 * width, 0.5 * width] if has_ideal else [0.0]
    colors = [IFUSE_COLOR, IDEAL_FUSION_COLOR] if has_ideal else [IFUSE_COLOR]
    pct_sets = [runtime_pct, ideal_pct] if has_ideal else [runtime_pct]

    # Y-limits from data first so label placement can stay inside the axis.
    ylim_args = [runtime_pct] + ([ideal_pct] if has_ideal else [])
    ylim = _ylim_with_bar_label_headroom(_ylim_speedup_pct_auto(*ylim_args))
    y_span = max(ylim[1] - ylim[0], 1e-6)
    # For near-zero bars, lift the label a little — but keep it inside ylim
    # (old fixed y=5.5 blew up bbox_inches='tight' into a tall empty figure).
    tiny_label_y = min(max(runtime_pct) * 0.15 + 0.05, ylim[1] * 0.85) if max(runtime_pct) < ARROW_THRESHOLD else None

    for i in range(len(labels)):
        for bar_offset, color, pct_list in zip(offsets, colors, pct_sets):
            val = pct_list[i]
            if 0 <= val < ARROW_THRESHOLD and tiny_label_y is not None:
                ax.annotate(
                    "",
                    xy=(i + bar_offset, 0),
                    xytext=(i + bar_offset, tiny_label_y),
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.5, mutation_scale=12),
                    zorder=10,
                )
                label_y = tiny_label_y
                va = "bottom"
            elif val >= 0:
                label_y = val + 0.02 * y_span
                va = "bottom"
            else:
                label_y = val - 0.02 * y_span
                va = "top"
            ax.text(
                i + bar_offset,
                label_y,
                f"{val:.1f}%",
                ha="center",
                va=va,
                fontsize=18 if has_ideal else 20,
                fontfamily="serif",
                color="red" if val < 0 else "black",
                zorder=10,
            )

    if len(labels) > 1:
        ax.axvline(
            x=len(labels) - 1.5,
            color=MAROON_COLOR,
            linestyle="--",
            alpha=0.8,
            linewidth=2.5,
            zorder=2,
        )

    ax.axhline(0, color="black", linewidth=1.5, zorder=2)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=26, fontfamily="serif")
    ax.get_xticklabels()[-1].set_weight("bold")
    ax.set_ylabel(
        "Speedup (%)\n(normalized to no-fusion)",
        fontsize=26,
        fontfamily="serif",
    )
    ax.set_ylim(ylim[0], ylim[1])
    # Fine ticks when all speedups are tiny.
    if y_span < 2.0:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.1f}"))
    else:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="y", labelsize=20)

    legend = ax.legend(
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="upper left",
        fontsize=26,
        edgecolor="black",
    )
    legend.get_frame().set_linewidth(2.0)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(0.95)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    fig.tight_layout()
    for suffix in ("png", "pdf", "eps"):
        fig.savefig(f"{output_stem}.{suffix}", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HPCA-style IPC speedup: runtime I-Fuse vs baseline (optional ideal)."
    )
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=Path("/users/deepmish/scarab/src/simulations/baseline"),
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path("/users/deepmish/scarab/src/simulations/runtime-ifuse"),
    )
    parser.add_argument(
        "--ideal-fusion-dir",
        type=Path,
        default=None,
        help="Optional ideal-fusion experiment dir. Omit to plot runtime I-Fuse only.",
    )
    parser.add_argument(
        "--workloads-db",
        type=Path,
        default=INFRA_DIR / "workloads" / "workloads_db.json",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--require-ideal",
        action="store_true",
        help="Skip apps that are missing ideal-fusion simpoints.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Plot apps using only simpoints present in both configs (renormalize weights).",
    )
    parser.add_argument(
        "--from-runtime-dir",
        action="store_true",
        help=(
            "Discover apps from {runtime-dir}/runtime_ifuse (plot everything present there) "
            "instead of the fixed APPS list."
        ),
    )
    args = parser.parse_args()

    output_dir = args.output_dir or DEFAULT_COMPLETED_RUNTIME_IFUSE_IPC_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    with args.workloads_db.open() as fh:
        db = json.load(fh)["datacenter"]["datacenter"]

    results: list[tuple[str, float, float, float | None, float, float | None]] = []
    skipped: list[str] = []
    require_ideal = args.require_ideal and args.ideal_fusion_dir is not None

    if args.from_runtime_dir:
        disk_apps = discover_apps_with_stats(args.runtime_dir, "runtime_ifuse")
        # Keep APPS order first, then any extras found on disk.
        apps = [a for a in APPS if a in disk_apps]
        apps += [a for a in disk_apps if a not in apps]
        if not apps:
            raise SystemExit(
                f"No apps with core.stat.0.csv under {args.runtime_dir}/runtime_ifuse"
            )
        print(f"Apps from runtime_ifuse ({len(apps)}): {', '.join(apps)}")
    else:
        apps = list(APPS)

    for app in apps:
        if app in db:
            simpoints = db[app]["simpoints"]
            weights = {str(sp["cluster_id"]): float(sp["weight"]) for sp in simpoints}
        else:
            # Equal-weight whatever simpoints exist on disk under runtime_ifuse.
            cids = discover_simpoint_ids(args.runtime_dir, "runtime_ifuse", app)
            if not cids:
                skipped.append(f"{app}: not in workloads_db and no runtime_ifuse stats")
                continue
            weights = {cid: 1.0 for cid in cids}
            skipped.append(f"{app}: not in workloads_db; using equal weights on {cids}")

        collected = collect_app_ipc(
            args.baseline_dir,
            args.runtime_dir,
            args.ideal_fusion_dir,
            app,
            weights,
            require_ideal=require_ideal,
            allow_partial=args.allow_partial,
        )
        if collected is None:
            expected = set(weights)
            missing = []
            for label, root, cfg in [
                ("baseline", args.baseline_dir, "baseline"),
                ("runtime_ifuse", args.runtime_dir, "runtime_ifuse"),
            ]:
                present = {
                    cid
                    for cid in weights
                    if read_ipc(ipc_path(root, cfg, app, cid)) is not None
                }
                if present != expected:
                    missing.append(f"{label} missing={sorted(expected - present)}")
            if require_ideal and args.ideal_fusion_dir is not None:
                present = {
                    cid
                    for cid in weights
                    if read_ipc(ipc_path(args.ideal_fusion_dir, "ideal_fusion", app, cid))
                    is not None
                }
                if present != expected:
                    missing.append(f"ideal_fusion missing={sorted(expected - present)}")
            skipped.append(f"{app}: " + "; ".join(missing))
            continue

        baseline, runtime, ideal, used_weights = collected
        base_avg = weighted_ipc(baseline, used_weights)
        runtime_avg = weighted_ipc(runtime, used_weights)
        ideal_avg = weighted_ipc(ideal, used_weights) if ideal is not None else None
        results.append(
            (
                app,
                base_avg,
                runtime_avg,
                ideal_avg,
                100.0 * (runtime_avg / base_avg - 1.0),
                100.0 * (ideal_avg / base_avg - 1.0) if ideal_avg is not None else None,
            )
        )

    if not results:
        raise SystemExit("No workloads are complete in baseline and runtime_ifuse.")

    summary_path = output_dir / "ipc_completed_speedup_summary.csv"
    with summary_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "baseline_ipc",
                "runtime_ifuse_ipc",
                "ideal_fusion_ipc",
                "runtime_ifuse_speedup_pct",
                "ideal_fusion_speedup_pct",
            ]
        )
        for app, base, runtime, ideal, runtime_spd, ideal_spd in results:
            writer.writerow([app, base, runtime, ideal or "", runtime_spd, ideal_spd or ""])

    workloads = [row[0] for row in results]
    runtime_speedups = [row[4] for row in results]
    ideal_speedups = [row[5] for row in results]
    has_ideal = all(v is not None for v in ideal_speedups)
    output_stem = output_dir / "ipc_completed_speedup"
    plot_speedup_bars(
        workloads,
        runtime_speedups,
        [float(v) for v in ideal_speedups] if has_ideal else None,
        output_stem,
    )

    print("Completed workloads:", ", ".join(workloads))
    if skipped:
        print("Skipped incomplete workloads:")
        for reason in skipped:
            print(" ", reason)
    msg = (
        "Average speedup: runtime_ifuse "
        f"{sum(runtime_speedups) / len(runtime_speedups):+.2f}%"
    )
    if has_ideal:
        msg += (
            f", ideal_fusion {sum(float(v) for v in ideal_speedups) / len(ideal_speedups):+.2f}%"
        )
    print(msg)
    print(f"Wrote {output_stem}.png")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
