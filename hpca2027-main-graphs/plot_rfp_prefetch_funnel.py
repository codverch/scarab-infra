#!/usr/bin/env python3
"""Paper-style RFP prefetch funnel: injected, executed, useful (% of on-path loads).

Simpoint-weighted fractions use RFP_ALL_LOADS_count as the denominator (on-path
loads seen at rename), matching the ISCA'22 RFP paper breakdown:
  - Injected:  RFP_PREFETCH_INJECTED_count
  - Executed:  RFP_PREFETCH_EXECUTED_count
  - Useful:    RFP_PREFETCH_USEFUL_count

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_rfp_prefetch_funnel.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --rfp-config rfp_24kb \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/rfp_prefetch_funnel
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    BAR_EDGE_WIDTH,
    DEFAULT_SCARAB_ROOT,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-main-graphs-results"
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "rfp_prefetch_funnel"

RFP_STAT = "rfp.stat.0.csv"
WORKLOADS = [
    "appworld",
    "bfs",
    "clickhouse",
    "core_bench",
    "dfs",
    "duckdb",
    "pagerank",
    "rocksdb",
    "terminal_bench",
]
RFP_CONFIGS = ["rfp_6kb", "rfp_12kb", "rfp_18kb", "rfp_24kb"]

CLR_INJECTED = "#C9A0DC"
CLR_EXECUTED = "#9B59B6"
CLR_USEFUL = RFP_COLOR

FIGSIZE = (28.0, 10.0)
BAR_WIDTH = 0.22
GROUP_WIDTH = 0.75
OUTPUT_DPI = 300


@dataclass
class PrefetchFunnel:
    injected_pct: float
    executed_pct: float
    useful_pct: float
    trace_count: int


def stat_value(stat_csv: Path, stat_name: str) -> float | None:
    if not stat_csv.is_file():
        return None
    with stat_csv.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            if row[0].strip() != stat_name:
                continue
            try:
                return float(row[2].strip())
            except ValueError:
                return None
    return None


def simpoint_funnel(
    simulations_root: Path,
    rfp_config: str,
    workload: str,
    cluster_id: str,
) -> PrefetchFunnel | None:
    sim_dir = find_simpoint_dir(
        simulations_root,
        rfp_config,
        workload,
        cluster_id,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
    )
    if sim_dir is None:
        return None
    rfp_csv = sim_dir / RFP_STAT
    all_loads = stat_value(rfp_csv, "RFP_ALL_LOADS_count")
    injected = stat_value(rfp_csv, "RFP_PREFETCH_INJECTED_count")
    executed = stat_value(rfp_csv, "RFP_PREFETCH_EXECUTED_count")
    useful = stat_value(rfp_csv, "RFP_PREFETCH_USEFUL_count")
    if None in (all_loads, injected, executed, useful) or all_loads <= 0:
        return None
    return PrefetchFunnel(
        injected_pct=100.0 * injected / all_loads,
        executed_pct=100.0 * executed / all_loads,
        useful_pct=100.0 * useful / all_loads,
        trace_count=1,
    )


def workload_funnel(
    simulations_root: Path,
    rfp_config: str,
    workload: str,
    sp_weights: dict[tuple[str, str], float],
) -> PrefetchFunnel | None:
    inj = exe = use = 0.0
    weight_sum = 0.0
    trace_count = 0
    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        row = simpoint_funnel(simulations_root, rfp_config, wl, cluster_id)
        if row is None:
            continue
        inj += weight * row.injected_pct
        exe += weight * row.executed_pct
        use += weight * row.useful_pct
        weight_sum += weight
        trace_count += 1
    if trace_count == 0 or weight_sum <= 0:
        return None
    return PrefetchFunnel(
        injected_pct=inj / weight_sum,
        executed_pct=exe / weight_sum,
        useful_pct=use / weight_sum,
        trace_count=trace_count,
    )


def write_csv(path: Path, config: str, rows: list[tuple[str, PrefetchFunnel]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "config",
                "workload",
                "injected_pct",
                "executed_pct",
                "useful_pct",
                "trace_count",
            ]
        )
        for workload, row in rows:
            writer.writerow(
                [
                    config,
                    workload,
                    f"{row.injected_pct:.4f}",
                    f"{row.executed_pct:.4f}",
                    f"{row.useful_pct:.4f}",
                    row.trace_count,
                ]
            )


def plot_funnel(
    rows: list[tuple[str, PrefetchFunnel]],
    output_dir: Path,
    *,
    config: str,
    title_suffix: str = "",
) -> None:
    if not rows:
        raise SystemExit("No data to plot")

    avg = PrefetchFunnel(
        injected_pct=sum(r.injected_pct for _, r in rows) / len(rows),
        executed_pct=sum(r.executed_pct for _, r in rows) / len(rows),
        useful_pct=sum(r.useful_pct for _, r in rows) / len(rows),
        trace_count=sum(r.trace_count for _, r in rows),
    )
    plot_rows = rows + [("Average", avg)]
    labels = [rename_workload(wl) for wl, _ in rows] + ["Average"]
    x = np.arange(len(plot_rows))

    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
            "legend.fontsize": IPC_LEGEND_FONT,
        }
    )
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    offsets = [-BAR_WIDTH, 0.0, BAR_WIDTH]
    series = [
        ("Injected", [r.injected_pct for _, r in plot_rows], CLR_INJECTED),
        ("Executed", [r.executed_pct for _, r in plot_rows], CLR_EXECUTED),
        ("Useful", [r.useful_pct for _, r in plot_rows], CLR_USEFUL),
    ]
    for offset, (label, values, color) in zip(offsets, series):
        ax.bar(
            x + offset,
            values,
            width=BAR_WIDTH,
            label=label,
            color=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )

    if len(plot_rows) > 1:
        ax.axvline(
            len(plot_rows) - 1.5,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            linewidth=2.0,
            alpha=0.8,
            zorder=1,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel(
        "% of on-path memory loads",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.set_ylim(0.0, min(105.0, max(max(r.injected_pct for _, r in plot_rows) * 1.15, 50)))
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    title = f"RFP prefetch funnel ({config})"
    if title_suffix:
        title += f" — {title_suffix}"
    ax.set_title(title, fontsize=IPC_AXIS_LABEL_FONT, pad=20)
    ax.legend(
        frameon=True,
        loc="upper right",
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        fancybox=False,
    )
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.subplots_adjust(bottom=0.22, top=0.90, left=0.08, right=0.99)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"rfp_prefetch_funnel_{config}"
    fig.savefig(output_dir / f"{stem}.png", dpi=OUTPUT_DPI, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", dpi=OUTPUT_DPI, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot RFP injected/executed/useful funnel.")
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
    parser.add_argument("--rfp-config", default="rfp_24kb")
    parser.add_argument("--all-configs", action="store_true")
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    sp_weights = load_simpoint_trace_weights(args.trace_root, WORKLOADS)
    configs = RFP_CONFIGS if args.all_configs else [args.rfp_config]

    for config in configs:
        rows: list[tuple[str, PrefetchFunnel]] = []
        for workload in WORKLOADS:
            row = workload_funnel(args.simulations_root, config, workload, sp_weights)
            if row is None:
                print(f"  skip {workload} ({config}): missing stats")
                continue
            rows.append((workload, row))
            print(
                f"  {config:10s} {workload:16s} "
                f"inj={row.injected_pct:5.1f}% exe={row.executed_pct:5.1f}% use={row.useful_pct:5.1f}%"
            )
        if not rows:
            continue
        write_csv(args.output_dir / f"rfp_prefetch_funnel_{config}.csv", config, rows)
        plot_funnel(rows, args.output_dir, config=config)
        print(f"  wrote {args.output_dir}/rfp_prefetch_funnel_{config}.png")


if __name__ == "__main__":
    main()
