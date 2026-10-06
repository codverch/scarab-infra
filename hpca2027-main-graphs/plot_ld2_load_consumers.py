#!/usr/bin/env python3
"""How many fused LOAD2 consumers are loads, for I-Fuse and 1-cycle-delayed I-Fuse.

A load that consumes LOAD2's value uses it as (part of) its address, as in a
pointer chase LOAD1 -> LOAD2 -> LOAD3. The 1-cycle LOAD2 wake delay then pushes
that load's whole cache access back a cycle.

Counters (ifuse.stat.0.csv, simpoint-weighted, simpoints present in both configs):
  all consumers      = IFUSE_LD2_CONSUMERS_{WAITING, RENAMED_IN_DELAY, RENAMED_AFTER_READY}
  critical consumers = IFUSE_LD2_CONSUMERS_{WAITING, RENAMED_IN_DELAY}_CRITICAL
                       (LOAD2 was the consumer's last source)
  load consumers     = the same counters with the _LOAD suffix

Two panels: the share of LOAD2 consumers that are loads, among all consumers
and among critical consumers.

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ld2_load_consumers.py \\
  --ifuse-dir /users/deepmish/scarab/src/simulations/llc-ifuse \\
  --ifuse-1cycle-dir /users/deepmish/scarab/src/simulations/llc-ifuse1c \\
  --trace-root /dev/shm/ifuse \\
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/ld2-load-consumers
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.patches import Patch

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

import plot_ipc  # noqa: E402
from plot_fusion_fraction import stat_count_from_csv  # noqa: E402
from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IFUSE_1CYCLE_COLOR,
    IFUSE_COLOR,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    register_noto_serif,
    rename_workload,
)

IFUSE_STAT = "ifuse.stat.0.csv"
P = "IFUSE_LD2_CONSUMERS_"
ALL_STATS = (P + "WAITING", P + "RENAMED_IN_DELAY", P + "RENAMED_AFTER_READY")
CRITICAL_STATS = (P + "WAITING_CRITICAL", P + "RENAMED_IN_DELAY_CRITICAL")
FUSED_STAT = "IFUSE_FUSED_LOADS"
STATS = (*ALL_STATS, *CRITICAL_STATS, *(s + "_LOAD" for s in ALL_STATS + CRITICAL_STATS),
         FUSED_STAT)

# (config key, legend label, color) — left to right within each app.
SERIES: tuple[tuple[str, str, str], ...] = (
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ifuse_1cycle", "I-Fuse (1-cycle delayed)", IFUSE_1CYCLE_COLOR),
)
# (key, y label) — top to bottom.
PANELS: tuple[tuple[str, str], ...] = (
    ("load_pct", "Loads among all\nLOAD2 consumers (%)"),
    ("critical_load_pct", "Loads among critical\nLOAD2 consumers (%)"),
)

BAR_WIDTH = 3.6
APP_STEP = 10.0
AVERAGE_GAP = 2.4
FIGSIZE = (72.0, 38.0)
AXIS_FONT = IPC_TICK_FONT
LEGEND_FONT = 100
VALUE_FONT = 60
OUTPUT_DPI = 300
OUTPUT_STEM = "ld2-load-consumers"


def weighted_counts(
    workload: str,
    sp_weights: dict[tuple[str, str], float],
    config_dirs: dict[str, Path],
) -> tuple[dict[str, dict[str, float]], int] | None:
    """Returns {config: {stat: weighted count}} over simpoints present in every config."""
    totals = {cfg: {stat: 0.0 for stat in STATS} for cfg in config_dirs}
    trace_count = 0
    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        values: dict[str, dict[str, float]] = {}
        for cfg, path in config_dirs.items():
            sim_dir = find_simpoint_dir(
                path, cfg, workload, cluster_id, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE
            )
            if sim_dir is None:
                break
            counts = {s: stat_count_from_csv(sim_dir / IFUSE_STAT, f"{s}_count") for s in STATS}
            if any(v is None for v in counts.values()):
                break
            values[cfg] = counts
        if len(values) != len(config_dirs):
            continue
        for cfg, counts in values.items():
            for stat, value in counts.items():
                totals[cfg][stat] += weight * value
        trace_count += 1
    if trace_count == 0:
        return None
    return totals, trace_count


def shares(c: dict[str, float]) -> dict[str, float]:
    all_n = sum(c[s] for s in ALL_STATS)
    crit_n = sum(c[s] for s in CRITICAL_STATS)
    return {
        "consumers": all_n,
        "critical": crit_n,
        "loads": sum(c[s + "_LOAD"] for s in ALL_STATS),
        "critical_loads": sum(c[s + "_LOAD"] for s in CRITICAL_STATS),
        "fused": c[FUSED_STAT],
        "load_pct": 100.0 * sum(c[s + "_LOAD"] for s in ALL_STATS) / all_n if all_n else 0.0,
        "critical_load_pct": (100.0 * sum(c[s + "_LOAD"] for s in CRITICAL_STATS) / crit_n
                              if crit_n else 0.0),
    }


def plot(rows: list[tuple[str, dict[str, dict[str, float]]]], output_dir: Path) -> None:
    """rows: (display name, {config: shares}), Average last."""
    labels = [name for name, _ in rows]
    x_apps = np.arange(len(rows) - 1, dtype=float) * APP_STEP
    offsets = [(i - (len(SERIES) - 1) / 2.0) * BAR_WIDTH for i in range(len(SERIES))]
    cluster_half = max(abs(o) for o in offsets) + BAR_WIDTH / 2.0
    avg_x = float(x_apps[-1] + 2.0 * cluster_half + AVERAGE_GAP)
    x = np.append(x_apps, avg_x)
    separator_x = float(x_apps[-1] + cluster_half + AVERAGE_GAP * 0.5)

    plt.rcParams.update({"font.family": plot_ipc.FONT_FAMILY})
    fig, axes = plt.subplots(len(PANELS), 1, figsize=FIGSIZE, sharex=True)
    for ax, (key, ylabel) in zip(axes, PANELS):
        ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
        for (cfg, _, color), offset in zip(SERIES, offsets):
            values = np.array([row[cfg][key] for _, row in rows])
            ax.bar(x + offset, values, BAR_WIDTH, color=color, edgecolor="black",
                   linewidth=BAR_EDGE_WIDTH, zorder=3)
            for xi, value in zip(x + offset, values):
                ax.text(xi, value + 1.5, f"{value:.0f}", ha="center", va="bottom",
                        fontsize=VALUE_FONT, rotation=90, zorder=4)
        ax.axvline(x=separator_x, color=AVERAGE_SEPARATOR_COLOR, linestyle="--",
                   linewidth=AVERAGE_SEPARATOR_WIDTH, zorder=2)
        ax.set_ylim(0.0, 119.0)
        ax.yaxis.set_major_locator(mticker.MultipleLocator(25))
        ax.set_ylabel(ylabel, fontsize=AXIS_FONT * 0.8)
        ax.tick_params(axis="y", labelsize=AXIS_FONT, colors="black")
        ax.tick_params(axis="x", length=0)
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(BAR_EDGE_WIDTH)
    bottom = axes[-1]
    bottom.set_xticks(x)
    bottom.set_xticklabels(labels, rotation=45, ha="right", color="black")
    bottom.tick_params(axis="x", labelsize=AXIS_FONT, length=0, pad=14)
    for label in bottom.get_xticklabels():
        if label.get_text() == "Average":
            label.set_weight("bold")
    bottom.set_xlim(x[0] - cluster_half - 2.5, x[-1] + cluster_half + 2.5)
    fig.align_ylabels(axes)

    handles = [Patch(facecolor=color, edgecolor="black", linewidth=BAR_EDGE_WIDTH, label=label)
               for _, label, color in SERIES]
    legend = fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.9),
                        ncol=len(SERIES), fontsize=LEGEND_FONT, frameon=True, fancybox=False,
                        edgecolor="black", handlelength=1.4, columnspacing=1.2)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    plt.subplots_adjust(top=0.88, hspace=0.12)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{OUTPUT_STEM}.{ext}", dpi=OUTPUT_DPI, bbox_inches="tight",
                    pad_inches=0.08)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ifuse-dir", type=Path, required=True)
    parser.add_argument("--ifuse-1cycle-dir", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exclude-workloads", nargs="*", default=[])
    args = parser.parse_args()

    config_dirs = {"ifuse": args.ifuse_dir, "ifuse_1cycle": args.ifuse_1cycle_dir}
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    rows: list[tuple[str, dict[str, dict[str, float]]]] = []
    summary: list[dict[str, object]] = []
    for workload in workloads:
        result = weighted_counts(workload, sp_weights, config_dirs)
        if result is None:
            print(f"  skip {workload}: no simpoint with stats in both configs")
            continue
        totals, trace_count = result
        row = {cfg: shares(totals[cfg]) for cfg in config_dirs}
        rows.append((rename_workload(workload), row))
        for cfg in config_dirs:
            summary.append({"workload": workload, "config": cfg, "simpoints": trace_count,
                            **{k: f"{v:.2f}" for k, v in row[cfg].items()}})
    if not rows:
        raise SystemExit("No workloads with stats in both configs.")

    avg = {cfg: {key: sum(r[cfg][key] for _, r in rows) / len(rows) for key, _ in PANELS}
           for cfg in config_dirs}
    rows.append(("Average", avg))
    for cfg in config_dirs:
        summary.append({"workload": "average", "config": cfg, "simpoints": "",
                        **{key: f"{avg[cfg][key]:.2f}" for key, _ in PANELS}})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fields = ["workload", "config", "simpoints", "consumers", "critical", "loads",
              "critical_loads", "fused", "load_pct", "critical_load_pct"]
    with (args.output_dir / f"{OUTPUT_STEM}_summary.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(summary)
    plot(rows, args.output_dir)

    print(f"{'':16s}" + "".join(f"{label[:12]:>14s}{'':>14s}" for _, label, _ in SERIES))
    print(f"{'':16s}" + "".join(f"{'loads/all':>14s}{'loads/crit':>14s}" for _ in SERIES))
    for name, row in rows:
        print(f"{name:16s}" + "".join(f"{row[cfg]['load_pct']:13.1f}%{row[cfg]['critical_load_pct']:13.1f}%"
                                      for cfg, _, _ in SERIES))
    print(f"Outputs in {args.output_dir}")


if __name__ == "__main__":
    main()
