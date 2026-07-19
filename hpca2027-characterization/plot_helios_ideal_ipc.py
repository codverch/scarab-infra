#!/usr/bin/env python3
"""Simpoint-weighted Helios + Ideal Fusion IPC speedup, normalized to baseline.

Expects:
  {simulations}/baseline/baseline/datacenter/datacenter/<app>/<chunk>/
  {simulations}/helios/<app>/<chunk>/                         (flat)
  {simulations}/ideal-fusion-pass2/pass2/datacenter/datacenter/<app>/<chunk>/

Example:
  python3 hpca2027-characterization/plot_helios_ideal_ipc.py \\
    --simulations-root /users/deepmish/scarab/src/simulations \\
    --output-dir hpca2027-characterization
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
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

HELIOS_COLOR = "#E67E22"  # Atre et al. SIGCOMM'22–style orange
IDEAL_COLOR = "#2E7D32"  # green
CATEGORY_GAP = 1.05
GROUP_GAP = 0.40
SUMMARY_GAP = 0.05
BAR_WIDTH = 0.38
AXIS_FONT = 34
GROUP_FONT = 30
APP_FONT = 28
FIGSIZE = (24.0, 11.9)
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
    """Resolve nested (baseline/ideal) or flat (helios) layouts."""
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
    left_pad = 0.18
    right_pad = 0.14
    ax.set_xlim(x_min - BAR_WIDTH - left_pad, x_max + BAR_WIDTH + right_pad)
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


def plot_speedup(
    apps: list[str],
    helios_pct: list[float],
    ideal_pct: list[float],
    output_dir: Path,
) -> None:
    # Floor at 0 so the figure never shows slowdowns.
    helios_pct = [max(0.0, float(v)) for v in helios_pct]
    ideal_pct = [max(0.0, float(v)) for v in ideal_pct]

    by_name = {
        app: (h, i) for app, h, i in zip(apps, helios_pct, ideal_pct)
    }
    positions_h: list[float] = []
    positions_i: list[float] = []
    values_h: list[float] = []
    values_i: list[float] = []
    app_labels: list[str] = []
    tick_positions: list[float] = []
    group_spans: list[tuple[float, float, str]] = []
    group_separators: list[float] = []

    x = 0.0
    group_idx = 0
    for suite_name, members in WORKLOAD_GROUPS:
        present = [wl for wl in members if wl in by_name]
        if not present:
            continue
        group_start = x
        if group_idx > 0:
            group_separators.append(group_start - BAR_WIDTH - 0.22)
        for wl in present:
            h, i = by_name[wl]
            positions_h.append(x - BAR_WIDTH / 2.0)
            positions_i.append(x + BAR_WIDTH / 2.0)
            values_h.append(h)
            values_i.append(i)
            tick_positions.append(x)
            app_labels.append(SHORT_LABELS.get(wl, wl))
            x += CATEGORY_GAP
        group_spans.append((group_start, x - CATEGORY_GAP, suite_name))
        x += GROUP_GAP
        group_idx += 1

    x += SUMMARY_GAP
    avg_x = x
    avg_h = float(np.mean(helios_pct)) if helios_pct else 0.0
    avg_i = float(np.mean(ideal_pct)) if ideal_pct else 0.0
    positions_h.append(avg_x - BAR_WIDTH / 2.0)
    positions_i.append(avg_x + BAR_WIDTH / 2.0)
    values_h.append(avg_h)
    values_i.append(avg_i)
    tick_positions.append(avg_x)
    app_labels.append(SUMMARY_XTICK)
    summary_sep = avg_x - CATEGORY_GAP / 2.0 - SUMMARY_GAP / 2.0

    fig_width = max(FIGSIZE[0], len(tick_positions) * 1.15 + len(group_spans) * 0.4)
    plt.rcParams.update(
        {"font.size": 15, "font.family": "serif", "axes.labelsize": AXIS_FONT}
    )
    fig, ax = plt.subplots(figsize=(fig_width, FIGSIZE[1]))

    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ax.bar(
        positions_h,
        values_h,
        BAR_WIDTH,
        label="Helios",
        color=HELIOS_COLOR,
        edgecolor="black",
        linewidth=1.5,
        zorder=3,
    )
    ax.bar(
        positions_i,
        values_i,
        BAR_WIDTH,
        label="Ideal fusion",
        color=IDEAL_COLOR,
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

    y_max = max(values_h + values_i) if (values_h or values_i) else 10.0
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
        fontsize=GROUP_FONT,
        edgecolor="black",
        facecolor="white",
    )
    legend.get_frame().set_linewidth(1.5)
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.0)

    fig.tight_layout()
    for stem in ("ipc-helios-ideal",):
        fig.savefig(output_dir / f"{stem}.png", bbox_inches="tight", dpi=300)
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
        print(f"Wrote {output_dir / stem}.png")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIM_ROOT)
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default="baseline")
    parser.add_argument("--helios-config", default="helios")
    parser.add_argument("--ideal-fusion-config", default="pass2")
    parser.add_argument("--output-dir", type=Path, default=GRAPH_DIR)
    args = parser.parse_args()

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or (sim_root / "baseline")
    helios_dir = args.helios_dir or (sim_root / "helios")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion-pass2")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    workloads = list(SIMPOINT_WORKLOADS)
    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Loading IPC from sim directories...")
    baseline_ipc = load_experiment_ipc(
        baseline_dir, args.baseline_config, sp_weights, workloads, "baseline"
    )
    helios_ipc = load_experiment_ipc(
        helios_dir, args.helios_config, sp_weights, workloads, "helios"
    )
    ideal_ipc = load_experiment_ipc(
        ideal_dir, args.ideal_fusion_config, sp_weights, workloads, "ideal fusion"
    )

    epochs = {
        "baseline": (baseline_dir, args.baseline_config),
        "helios": (helios_dir, args.helios_config),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config),
    }
    complete, refs = check_coverage(
        epochs, workloads, sp_weights, output_dir / "ipc_simpoint_coverage.txt"
    )
    if not complete:
        raise SystemExit("No apps have complete coverage across baseline/helios/ideal.")

    apps = [wl for wl in workloads if wl in complete]
    helios_pct: list[float] = []
    ideal_pct: list[float] = []
    rows: list[dict[str, float | str]] = []

    print(
        f"\n{'App':<18} {'BaseIPC':>8} {'Helios':>8} {'Ideal':>8} "
        f"{'Helios%':>9} {'Ideal%':>9}"
    )
    print("-" * 72)
    for wl in apps:
        ref = refs[wl]
        bp = collect_trace_pairs(
            baseline_ipc, sp_weights, config=args.baseline_config,
            workload=wl, reference_traces=ref,
        )
        hp = collect_trace_pairs(
            helios_ipc, sp_weights, config=args.helios_config,
            workload=wl, reference_traces=ref,
        )
        ip = collect_trace_pairs(
            ideal_ipc, sp_weights, config=args.ideal_fusion_config,
            workload=wl, reference_traces=ref,
        )
        b_avg = weighted_avg(bp)
        h_avg = weighted_avg(hp)
        i_avg = weighted_avg(ip)
        h_norm = h_avg / b_avg if b_avg else float("nan")
        i_norm = i_avg / b_avg if b_avg else float("nan")
        h_pct = 100.0 * (h_norm - 1.0)
        i_pct = 100.0 * (i_norm - 1.0)
        helios_pct.append(h_pct)
        ideal_pct.append(i_pct)
        rows.append(
            {
                "workload": wl,
                "baseline_ipc": b_avg,
                "helios_ipc": h_avg,
                "ideal_ipc": i_avg,
                "helios_norm": h_norm,
                "ideal_norm": i_norm,
                "helios_speedup_pct": h_pct,
                "ideal_speedup_pct": i_pct,
            }
        )
        print(
            f"{wl:<18} {b_avg:8.3f} {h_avg:8.3f} {i_avg:8.3f} "
            f"{h_pct:+8.2f}% {i_pct:+8.2f}%"
        )

    avg_h = float(np.mean(helios_pct))
    avg_i = float(np.mean(ideal_pct))
    print("-" * 72)
    print(f"{'Average':<18} {'':>8} {'':>8} {'':>8} {avg_h:+8.2f}% {avg_i:+8.2f}%")

    csv_path = output_dir / "ipc_helios_ideal.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        writer.writerow(
            {
                "workload": "Average",
                "baseline_ipc": "",
                "helios_ipc": "",
                "ideal_ipc": "",
                "helios_norm": "",
                "ideal_norm": "",
                "helios_speedup_pct": avg_h,
                "ideal_speedup_pct": avg_i,
            }
        )
    print(f"Wrote {csv_path}")

    plot_speedup(apps, helios_pct, ideal_pct, output_dir)


if __name__ == "__main__":
    main()
