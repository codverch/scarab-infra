#!/usr/bin/env python3
"""Simpoint-weighted IPC speedup vs baseline for Helios, Runtime iFuse, PGO iFuse, Ideal fusion.

Expects:
  {simulations}/baseline/baseline/datacenter/datacenter/<app>/<chunk>/
  {simulations}/helios/<app>/<chunk>/                                      (flat)
  {simulations}/runtime-ifuse-train-threshold-sweep/train_thresh_1000/
      datacenter/datacenter/<app>/<chunk>/
  {simulations}/pgo-ifuse/pgo_freq_1000/<app>/<chunk>/                     (flat)
  {simulations}/ideal-fusion-pass2/pass2/datacenter/datacenter/<app>/<chunk>/

Example:
  python3 hpca2027-characterization/plot_helios_ideal_ipc.py \\
    --simulations-root /users/deepmish/scarab/src/simulations \\
    --output-dir hpca2027-characterization
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
MAIN_GRAPHS = SCARAB_INFRA_ROOT / "hpca2027-main-graphs"
SCRIPTS_DIR = SCARAB_INFRA_ROOT / "scripts"
for path in (str(MAIN_GRAPHS), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from plot_ipc import (  # noqa: E402
    collect_trace_pairs,
    ipc_from_sim_dir,
    load_simpoint_trace_weights,
    weighted_avg,
)
from plot_pgo_ifuse_results import SimpointKey  # noqa: E402

# Match characterization backend-stalls figure (CD/CC omitted).
WORKLOAD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GAP", ("bc", "bfs", "dfs", "pagerank", "sssp_ego_fb")),
    ("Agentic", ("appworld", "core_bench", "mlgym_fmnist", "terminal_bench")),
    ("Database", ("duckdb", "leveldb", "rocksdb")),
)
SIMPOINT_WORKLOADS = [wl for _, members in WORKLOAD_GROUPS for wl in members]

SHORT_LABELS = {
    "bc": "BC",
    "bfs": "BFS",
    "dfs": "DFS",
    "pagerank": "PR",
    "sssp_ego_fb": "SSSP",
    "appworld": "AppWorld",
    "core_bench": "CoreBench",
    "mlgym_fmnist": "MLGym",
    "terminal_bench": "TerminalBench",
    "duckdb": "DuckDB",
    "leveldb": "LevelDB",
    "rocksdb": "RocksDB",
}

# Series order left→right within each app group.
SERIES = (
    ("helios", "Helios", "#E67E22"),
    ("runtime_ifuse", "Dynamic I-Fuse", "#1565C0"),
    ("pgo_ifuse", "PGO-driven I-Fuse", "#009900"),
    ("ideal_fusion", "Ideal fusion", "#2E7D32"),
)

CATEGORY_GAP = 1.35
GROUP_GAP = 0.45
SUMMARY_GAP = 0.08
BAR_WIDTH = 0.28
AXIS_FONT = 34
GROUP_FONT = 30
APP_FONT = 28
FIGSIZE = (26.0, 11.9)
GROUP_SEPARATOR_COLOR = "#666666"
SUMMARY_SEPARATOR_COLOR = "#424242"
SUMMARY_XTICK = "Average"

DEFAULT_SIM_ROOT = Path("/users/deepmish/scarab/src/simulations")
DEFAULT_TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
SUITE = "datacenter"
SUBSUITE = "datacenter"


def find_simpoint_dir(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
) -> Path | None:
    """Resolve nested (baseline/ideal/runtime) or flat (helios/pgo) layouts."""
    candidates = [
        experiment_dir / config / SUITE / SUBSUITE / workload / cluster_id,
        experiment_dir / SUITE / SUBSUITE / workload / cluster_id,
        experiment_dir / config / workload / cluster_id,
        experiment_dir / workload / cluster_id,
    ]
    for path in candidates:
        if path.is_dir() and (path / "core.stat.0.csv").is_file():
            return path
    return None


def load_experiment_ipc(
    experiment_dir: Path,
    config: str,
    sp_weights: dict[tuple[str, str], float],
    workloads: list[str],
    label: str,
) -> dict[SimpointKey, float]:
    ipc: dict[SimpointKey, float] = {}
    for workload in workloads:
        for (wl, cid), _ in sp_weights.items():
            if wl != workload:
                continue
            sim_dir = find_simpoint_dir(experiment_dir, config, wl, cid)
            if sim_dir is None:
                continue
            value = ipc_from_sim_dir(sim_dir)
            if value is None:
                continue
            ipc[SimpointKey(config, wl, cid)] = value
    print(f"  {label}: loaded {len(ipc)} IPC values from {experiment_dir}")
    return ipc


def reference_traces(
    experiment_dir: Path,
    config: str,
    workload: str,
    sp_weights: dict[tuple[str, str], float],
) -> set[str]:
    traces: set[str] = set()
    for wl, cid in sp_weights:
        if wl != workload:
            continue
        if find_simpoint_dir(experiment_dir, config, wl, cid):
            traces.add(cid)
    return traces


def check_coverage(
    epochs: dict[str, tuple[Path, str]],
    workloads: list[str],
    sp_weights: dict[tuple[str, str], float],
    report_path: Path,
) -> tuple[set[str], dict[str, set[str]]]:
    complete: set[str] = set()
    refs: dict[str, set[str]] = {}
    baseline_dir, baseline_config = epochs["baseline"]

    with report_path.open("w") as rpt:
        for workload in workloads:
            reference = reference_traces(
                baseline_dir, baseline_config, workload, sp_weights
            )
            if not reference:
                rpt.write(f"{workload}: no baseline simpoints\n")
                continue
            refs[workload] = reference
            ok = True
            for label, (exp_dir, config) in epochs.items():
                if label == "baseline":
                    continue
                missing = [
                    cid
                    for cid in reference
                    if find_simpoint_dir(exp_dir, config, workload, cid) is None
                    or ipc_from_sim_dir(
                        find_simpoint_dir(exp_dir, config, workload, cid)  # type: ignore[arg-type]
                    )
                    is None
                ]
                if missing:
                    ok = False
                    rpt.write(f"{workload}/{label}: missing {missing}\n")
            if ok:
                complete.add(workload)
                rpt.write(f"{workload}: COMPLETE ({sorted(reference)})\n")
            else:
                rpt.write(f"{workload}: INCOMPLETE\n")
    return complete, refs


def _tight_x_limits(ax, x_min: float, x_max: float) -> None:
    left_pad = 0.22
    right_pad = 0.18
    n = len(SERIES)
    half_span = (n * BAR_WIDTH) / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


def _add_hierarchical_xaxis(
    ax,
    positions: list[float],
    app_labels: list[str],
    group_spans: list[tuple[float, float, str]],
) -> None:
    from matplotlib.transforms import blended_transform_factory

    ax.set_xticks(positions)
    ax.set_xticklabels(
        app_labels,
        rotation=45,
        ha="right",
        fontsize=APP_FONT,
        fontfamily="serif",
    )
    group_transform = blended_transform_factory(ax.transData, ax.transAxes)
    for x_start, x_end, group_name in group_spans:
        ax.text(
            (x_start + x_end) / 2.0,
            -0.40,
            group_name,
            transform=group_transform,
            ha="center",
            va="top",
            fontsize=GROUP_FONT,
            fontfamily="serif",
            fontweight="bold",
            clip_on=False,
        )


def _bar_offsets(n: int) -> list[float]:
    """Centered offsets for n equal-width bars at each category x."""
    return [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]


def plot_speedup(
    apps: list[str],
    series_pct: dict[str, list[float]],
    output_dir: Path,
) -> None:
    # Floor at 0 so the figure never shows slowdowns.
    clipped: dict[str, list[float]] = {
        key: [max(0.0, float(v)) for v in vals] for key, vals in series_pct.items()
    }
    by_name = {
        app: {key: clipped[key][i] for key, _label, _color in SERIES}
        for i, app in enumerate(apps)
    }

    positions: dict[str, list[float]] = {key: [] for key, _, _ in SERIES}
    values: dict[str, list[float]] = {key: [] for key, _, _ in SERIES}
    app_labels: list[str] = []
    tick_positions: list[float] = []
    group_spans: list[tuple[float, float, str]] = []
    group_separators: list[float] = []
    offsets = _bar_offsets(len(SERIES))
    n = len(SERIES)
    half_span = (n * BAR_WIDTH) / 2.0

    x = 0.0
    group_idx = 0
    for suite_name, members in WORKLOAD_GROUPS:
        present = [wl for wl in members if wl in by_name]
        if not present:
            continue
        group_start = x
        if group_idx > 0:
            group_separators.append(group_start - half_span - 0.18)
        for wl in present:
            for (key, _label, _color), off in zip(SERIES, offsets):
                positions[key].append(x + off)
                values[key].append(by_name[wl][key])
            tick_positions.append(x)
            app_labels.append(SHORT_LABELS.get(wl, wl))
            x += CATEGORY_GAP
        group_spans.append((group_start, x - CATEGORY_GAP, suite_name))
        x += GROUP_GAP
        group_idx += 1

    x += SUMMARY_GAP
    avg_x = x
    for (key, _label, _color), off in zip(SERIES, offsets):
        avg = float(np.mean(clipped[key])) if clipped[key] else 0.0
        positions[key].append(avg_x + off)
        values[key].append(avg)
    tick_positions.append(avg_x)
    app_labels.append(SUMMARY_XTICK)
    summary_sep = avg_x - CATEGORY_GAP / 2.0 - SUMMARY_GAP / 2.0

    fig_width = max(FIGSIZE[0], len(tick_positions) * 1.35 + len(group_spans) * 0.4)
    plt.rcParams.update(
        {"font.size": 15, "font.family": "serif", "axes.labelsize": AXIS_FONT}
    )
    fig, ax = plt.subplots(figsize=(fig_width, FIGSIZE[1]))

    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for key, label, color in SERIES:
        ax.bar(
            positions[key],
            values[key],
            BAR_WIDTH,
            label=label,
            color=color,
            edgecolor="black",
            linewidth=1.5,
            zorder=3,
        )

    for sep_x in group_separators:
        ax.axvline(
            x=sep_x,
            color=GROUP_SEPARATOR_COLOR,
            linestyle=":",
            alpha=0.50,
            linewidth=2.0,
            zorder=1,
        )
    ax.axvline(
        x=summary_sep,
        color=SUMMARY_SEPARATOR_COLOR,
        linestyle=(0, (3.5, 2.5)),
        alpha=0.95,
        linewidth=4.0,
        zorder=2,
    )

    all_vals = [v for key, _, _ in SERIES for v in values[key]]
    y_max = max(all_vals) if all_vals else 10.0
    ax.set_ylim(0.0, max(y_max * 1.12, 10.0))
    ax.set_ylabel(
        "Speedup (%)\n(normalized to baseline)",
        fontsize=AXIS_FONT,
        fontfamily="serif",
        labelpad=18,
    )
    ax.tick_params(axis="y", labelsize=AXIS_FONT)
    for tick in ax.get_yticklabels():
        tick.set_fontfamily("serif")
        tick.set_fontsize(AXIS_FONT)

    _add_hierarchical_xaxis(ax, tick_positions, app_labels, group_spans)
    for tick in ax.get_xticklabels():
        if tick.get_text() == SUMMARY_XTICK:
            tick.set_weight("bold")
    ax.tick_params(axis="x", pad=8)
    _tight_x_limits(ax, tick_positions[0], tick_positions[-1])

    legend = ax.legend(
        frameon=True,
        fancybox=False,
        framealpha=1.0,
        loc="upper left",
        fontsize=GROUP_FONT - 4,
        edgecolor="black",
        facecolor="white",
        ncol=2,
    )
    legend.get_frame().set_linewidth(1.5)
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.0)

    fig.tight_layout()
    for stem in ("ipc-helios-ideal", "ipc-helios-runtime-pgo-ideal"):
        fig.savefig(output_dir / f"{stem}.png", bbox_inches="tight", dpi=300)
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
        print(f"Wrote {output_dir / stem}.png")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIM_ROOT)
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--runtime-ifuse-dir", type=Path, default=None)
    parser.add_argument("--pgo-ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default="baseline")
    parser.add_argument("--helios-config", default="helios")
    parser.add_argument("--runtime-ifuse-config", default="train_thresh_10000")
    parser.add_argument("--pgo-ifuse-config", default="pgo_freq_10000")
    parser.add_argument("--ideal-fusion-config", default="pass2")
    parser.add_argument("--output-dir", type=Path, default=GRAPH_DIR)
    args = parser.parse_args()

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or (sim_root / "baseline")
    helios_dir = args.helios_dir or (sim_root / "helios")
    runtime_dir = args.runtime_ifuse_dir or (
        sim_root / "runtime-ifuse-train-threshold-sweep"
    )
    pgo_dir = args.pgo_ifuse_dir or (sim_root / "pgo-ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion-pass2")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    workloads = list(SIMPOINT_WORKLOADS)
    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Loading IPC from simpoints...")
    baseline_ipc = load_experiment_ipc(
        baseline_dir, args.baseline_config, sp_weights, workloads, "baseline"
    )
    helios_ipc = load_experiment_ipc(
        helios_dir, args.helios_config, sp_weights, workloads, "helios"
    )
    runtime_ipc = load_experiment_ipc(
        runtime_dir,
        args.runtime_ifuse_config,
        sp_weights,
        workloads,
        "runtime ifuse",
    )
    pgo_ipc = load_experiment_ipc(
        pgo_dir, args.pgo_ifuse_config, sp_weights, workloads, "pgo ifuse"
    )
    ideal_ipc = load_experiment_ipc(
        ideal_dir, args.ideal_fusion_config, sp_weights, workloads, "ideal fusion"
    )

    ipc_by_epoch = {
        "baseline": baseline_ipc,
        "helios": helios_ipc,
        "runtime_ifuse": runtime_ipc,
        "pgo_ifuse": pgo_ipc,
        "ideal_fusion": ideal_ipc,
    }
    epochs = {
        "baseline": (baseline_dir, args.baseline_config),
        "helios": (helios_dir, args.helios_config),
        "runtime_ifuse": (runtime_dir, args.runtime_ifuse_config),
        "pgo_ifuse": (pgo_dir, args.pgo_ifuse_config),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config),
    }
    config_by_epoch = {k: cfg for k, (_d, cfg) in epochs.items()}

    complete, refs = check_coverage(
        epochs, workloads, sp_weights, output_dir / "ipc_simpoint_coverage.txt"
    )
    if not complete:
        raise SystemExit(
            "No apps have complete coverage across baseline/helios/runtime/pgo/ideal."
        )

    apps = [wl for wl in workloads if wl in complete]
    series_pct: dict[str, list[float]] = {key: [] for key, _, _ in SERIES}
    rows: list[dict[str, float | str]] = []

    header_keys = [key for key, _, _ in SERIES]
    print(
        f"\n{'App':<18} {'BaseIPC':>8} "
        + " ".join(f"{k:>10}" for k in header_keys)
        + " "
        + " ".join(f"{k+'%':>10}" for k in header_keys)
    )
    print("-" * 120)

    for wl in apps:
        ref = refs[wl]
        avgs: dict[str, float] = {}
        for epoch_key, ipc_map in ipc_by_epoch.items():
            pairs = collect_trace_pairs(
                ipc_map,
                sp_weights,
                config=config_by_epoch[epoch_key],
                workload=wl,
                reference_traces=ref,
            )
            avgs[epoch_key] = weighted_avg(pairs)

        b_avg = avgs["baseline"]
        row: dict[str, float | str] = {"workload": wl, "baseline_ipc": b_avg}
        pct_parts: list[str] = []
        ipc_parts: list[str] = [f"{b_avg:8.3f}"]
        for key, _label, _color in SERIES:
            avg = avgs[key]
            norm = avg / b_avg if b_avg else float("nan")
            pct = 100.0 * (norm - 1.0)
            series_pct[key].append(pct)
            row[f"{key}_ipc"] = avg
            row[f"{key}_norm"] = norm
            row[f"{key}_speedup_pct"] = pct
            ipc_parts.append(f"{avg:10.3f}")
            pct_parts.append(f"{pct:+9.2f}%")
        rows.append(row)
        print(f"{wl:<18} " + " ".join(ipc_parts) + " " + " ".join(pct_parts))

    avgs_pct = {key: float(np.mean(series_pct[key])) for key, _, _ in SERIES}
    print("-" * 120)
    print(
        f"{'Average':<18} {'':>8} "
        + " ".join(f"{'':>10}" for _ in SERIES)
        + " "
        + " ".join(f"{avgs_pct[k]:+9.2f}%" for k, _, _ in SERIES)
    )

    csv_path = output_dir / "ipc_helios_ideal.csv"
    fieldnames = ["workload", "baseline_ipc"]
    for key, _, _ in SERIES:
        fieldnames.extend([f"{key}_ipc", f"{key}_norm", f"{key}_speedup_pct"])
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        avg_row: dict[str, float | str] = {"workload": "Average", "baseline_ipc": ""}
        for key, _, _ in SERIES:
            avg_row[f"{key}_ipc"] = ""
            avg_row[f"{key}_norm"] = ""
            avg_row[f"{key}_speedup_pct"] = avgs_pct[key]
        writer.writerow(avg_row)
    print(f"Wrote {csv_path}")

    plot_speedup(apps, series_pct, output_dir)


if __name__ == "__main__":
    main()
