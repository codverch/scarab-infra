#!/usr/bin/env python3
"""Simpoint-weighted total load exec-fetch latency reduction for I-Fuse vs no-fusion.

Measures average fetch-to-execute latency (exec − fetch) across all on-path loads:

  no-fusion baseline (per simpoint):
    baseline_LD_EXEC_MINUS_FETCH_LATENCY / IFUSE_ALL_LOADS

  I-Fuse (per simpoint):
    ifuse_LD_EXEC_MINUS_FETCH_LATENCY / IFUSE_ALL_LOADS

Load count (IFUSE_ALL_LOADS) is read from ifuse.stat.0.csv; exec totals from core.stat.0.csv.

Per workload (simpoint-weighted averages A_no, A_ifuse):
  reduction_pct = 100 * (A_no - A_ifuse) / A_no

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_load_latency.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --baseline-dir /users/deepmish/scarab/src/simulations/rfp-baseline \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/load_latency
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    ARROW_THRESHOLD,
    AVERAGE_SEPARATOR_COLOR,
    BAR_EDGE_WIDTH,
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_LOAD_LATENCY_OUTPUT_DIR,
    DEFAULT_RFP_BASELINE_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    IFUSE_COLOR,
    SIMPOINT_WORKLOADS,
    check_simpoint_coverage,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

LOAD_EXEC_STAT = "LD_EXEC_MINUS_FETCH_LATENCY_count"
IFUSE_ALL_LOADS_STAT = "IFUSE_ALL_LOADS_count"

CORE_STAT_FILE = "core.stat.0.csv"
IFUSE_STAT_FILE = "ifuse.stat.0.csv"


@dataclass
class LoadLatencyResult:
    workload: str
    baseline_exec_fetch: float
    ifuse_exec_fetch: float
    ifuse_reduction_pct: float
    trace_count: int


def stat_count_from_csv(stat_csv: Path, stat_name: str) -> float | None:
    if not stat_csv.is_file():
        return None
    with stat_csv.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            if row[0].strip() == stat_name:
                try:
                    return float(row[2].strip())
                except ValueError:
                    return None
    return None


def simpoint_load_exec_fetch_avgs(
    baseline_dir: Path,
    ifuse_dir: Path,
    workload: str,
    cluster_id: str,
    *,
    baseline_config: str,
    ifuse_config: str,
    suite: str,
    subsuite: str,
) -> tuple[float, float] | None:
    baseline_sim = find_simpoint_dir(
        baseline_dir, baseline_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    ifuse_sim = find_simpoint_dir(
        ifuse_dir, ifuse_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if baseline_sim is None or ifuse_sim is None:
        return None

    baseline_exec = stat_count_from_csv(baseline_sim / CORE_STAT_FILE, LOAD_EXEC_STAT)
    ifuse_exec = stat_count_from_csv(ifuse_sim / CORE_STAT_FILE, LOAD_EXEC_STAT)
    onpath_loads = stat_count_from_csv(ifuse_sim / IFUSE_STAT_FILE, IFUSE_ALL_LOADS_STAT)

    if (
        baseline_exec is None
        or ifuse_exec is None
        or onpath_loads is None
        or baseline_exec <= 0
        or ifuse_exec <= 0
        or onpath_loads <= 0
    ):
        return None

    return baseline_exec / onpath_loads, ifuse_exec / onpath_loads


def _reduction_pct(baseline_avg: float, config_avg: float) -> float:
    return 100.0 * (baseline_avg - config_avg) / baseline_avg


def compute_workload_load_latency(
    workload: str,
    baseline_dir: Path,
    ifuse_dir: Path,
    reference_traces: set[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    baseline_config: str,
    ifuse_config: str,
    suite: str,
    subsuite: str,
) -> LoadLatencyResult | None:
    weighted_baseline = 0.0
    weighted_ifuse = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0 or cluster_id not in reference_traces:
            continue

        avgs = simpoint_load_exec_fetch_avgs(
            baseline_dir,
            ifuse_dir,
            workload,
            cluster_id,
            baseline_config=baseline_config,
            ifuse_config=ifuse_config,
            suite=suite,
            subsuite=subsuite,
        )
        if avgs is None:
            continue

        baseline_avg, ifuse_avg = avgs
        weighted_baseline += weight * baseline_avg
        weighted_ifuse += weight * ifuse_avg
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_baseline <= 0:
        return None

    baseline_exec_fetch = weighted_baseline / weight_sum
    ifuse_exec_fetch = weighted_ifuse / weight_sum

    return LoadLatencyResult(
        workload=workload,
        baseline_exec_fetch=baseline_exec_fetch,
        ifuse_exec_fetch=ifuse_exec_fetch,
        ifuse_reduction_pct=_reduction_pct(baseline_exec_fetch, ifuse_exec_fetch),
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[LoadLatencyResult]) -> None:
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_baseline_exec_fetch",
        "weighted_ifuse_exec_fetch",
        "ifuse_reduction_pct",
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "weighted_baseline_exec_fetch": f"{result.baseline_exec_fetch:.2f}",
                    "weighted_ifuse_exec_fetch": f"{result.ifuse_exec_fetch:.2f}",
                    "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                }
            )


def write_computation_log(path: Path, results: list[LoadLatencyResult]) -> None:
    with path.open("w") as fh:
        fh.write("Total load exec-fetch latency reduction (I-Fuse vs no-fusion)\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "Per simpoint:\n"
            "  baseline: LD_EXEC_MINUS_FETCH_LATENCY / IFUSE_ALL_LOADS (core + ifuse stat)\n"
            "  ifuse:    LD_EXEC_MINUS_FETCH_LATENCY / IFUSE_ALL_LOADS (core + ifuse stat)\n"
            "reduction_pct = 100 * (weighted_baseline_exec_fetch - weighted_ifuse_exec_fetch) "
            "/ weighted_baseline_exec_fetch\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(
                f"  weighted no-fusion exec-fetch: {result.baseline_exec_fetch:.2f} cycles\n"
            )
            fh.write(
                f"  weighted I-Fuse exec-fetch:    {result.ifuse_exec_fetch:.2f} cycles  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse reduction:  {ifuse_avg:.2f}%\n")


def plot_load_latency_reduction_bars(results: list[LoadLatencyResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt

    BAR_WIDTH_LOCAL = 0.62
    FIGSIZE = (16, 5)
    YLABEL_FONT = 26
    TICK_FONT = 24

    values = [result.ifuse_reduction_pct for result in results]
    avg = sum(values) / len(values) if values else float("nan")
    values.append(avg)

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))

    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "font.size": TICK_FONT,
            "axes.labelsize": YLABEL_FONT,
            "xtick.labelsize": TICK_FONT,
            "ytick.labelsize": TICK_FONT,
        }
    )
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    edge_colors = ["red" if val < 0 else "black" for val in values]
    edge_widths = [2.5 if val < 0 else BAR_EDGE_WIDTH for val in values]
    ax.bar(
        x,
        [max(0.0, val) for val in values],
        BAR_WIDTH_LOCAL,
        color=IFUSE_COLOR,
        edgecolor=edge_colors,
        linewidth=edge_widths,
        zorder=3,
    )

    positive_vals = [val for val in values if val > 0]
    ymax = max(positive_vals) if positive_vals else 15.0
    ymax = ymax * 1.08 + 1.0
    arrow_y = min(ymax * 0.12, 4.0)

    for i, val in enumerate(values):
        if not (0 <= val < ARROW_THRESHOLD):
            continue
        ax.annotate(
            "",
            xy=(i, 0),
            xytext=(i, arrow_y),
            arrowprops=dict(arrowstyle="->", color=IFUSE_COLOR, lw=1.5, mutation_scale=12),
            zorder=10,
        )
        ax.text(
            i - 0.12,
            arrow_y,
            f"{val:.1f}",
            ha="center",
            va="bottom",
            fontsize=TICK_FONT - 4,
            fontfamily=FONT_FAMILY,
            color=IFUSE_COLOR,
            zorder=10,
        )

    if len(display_apps) > 1:
        ax.axvline(
            x=len(display_apps) - 1.5,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            alpha=0.9,
            linewidth=2.5,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        display_apps,
        rotation=45,
        ha="right",
        fontsize=TICK_FONT,
        fontfamily=FONT_FAMILY,
    )
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    ax.set_ylabel(
        "I-Fuse load latency reduction\ndue to speculation\n"
        "(normalized to no-fusion) (%)",
        fontsize=YLABEL_FONT,
        fontfamily=FONT_FAMILY,
    )

    ax.set_ylim(0.0, ymax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    fig.subplots_adjust(left=0.14, bottom=0.28, right=0.99, top=0.97)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("load_latency",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted total load exec-fetch latency reduction for I-Fuse vs "
            "no-fusion baseline."
        )
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=None,
        help="No-fusion baseline directory (default: rfp-baseline)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/load_latency)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    baseline_dir = args.baseline_dir or DEFAULT_RFP_BASELINE_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_LOAD_LATENCY_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing total load exec-fetch latency reductions...")
    print(f"  baseline: {baseline_dir} (config={args.baseline_config})")
    print(f"  ifuse:    {ifuse_dir} (config={args.ifuse_config})")
    print(f"  output:   {output_dir}")

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
    }
    complete_apps, reference_by_workload = check_simpoint_coverage(
        directories,
        workloads,
        sp_weights,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
        report_path=output_dir / "load_latency_simpoint_coverage_report.txt",
    )
    if not complete_apps:
        raise SystemExit("No apps have complete simpoint files across baseline and ifuse.")

    results: list[LoadLatencyResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_load_latency(
            workload,
            baseline_dir,
            ifuse_dir,
            reference_by_workload[workload],
            sp_weights,
            baseline_config=args.baseline_config,
            ifuse_config=args.ifuse_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing load latency stats")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  ifuse={result.ifuse_reduction_pct:5.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete load latency data.")

    write_summary_csv(output_dir / "load_latency_summary.csv", results)
    write_computation_log(output_dir / "load_latency_computation_log.txt", results)
    plot_load_latency_reduction_bars(results, output_dir)

    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  I-Fuse mean reduction: {ifuse_avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'load_latency.png'}")
    print(f"  - {output_dir / 'load_latency.pdf'}")
    print(f"  - {output_dir / 'load_latency.eps'}")
    print(f"  - {output_dir / 'load_latency_summary.csv'}")
    print(f"  - {output_dir / 'load_latency_computation_log.txt'}")


if __name__ == "__main__":
    main()
