#!/usr/bin/env python3
"""Simpoint-weighted load data-wait latency reduction for RFP and I-Fuse vs no-fusion.

Data-wait latency isolates memory service time from front-end and scheduler wait:

  sum over retired on-path loads of (done_cycle - exec_cycle)

Unlike LD_EXEC_MINUS_FETCH_LATENCY (fetch-to-address-generation, which also contains
dispatch and scheduler queueing), this counter measures only the cycles a load spends
waiting for its data after address generation.

Per simpoint, all configurations divide by the SAME pre-fusion load population, so the
metric is "data-wait cycles per original architectural load":

  no-fusion:  LD_EXEC_TO_DONE_LATENCY   / LD_RETIRED_ONPATH   (core.stat.0.csv)
  RFP:        RFP_LD_EXEC_TO_DONE       / RFP_RETIRE_LOAD     (rfp.stat.0.csv)
  I-Fuse:     IFUSE_LD_EXEC_TO_DONE     / IFUSE_ALL_LOADS     (ifuse.stat.0.csv)

The three denominators are the same load population (verified equal per simpoint), and
each numerator's sub-counters sum to its total:
  IFUSE_LD_EXEC_TO_DONE = _FUSED + _DEMAND, RFP_LD_EXEC_TO_DONE = _SERVED + _DEMAND.

Helios and ideal fusion are NOT plotted: those builds do not emit an exec-to-done
counter. Re-running them with a build that has LD_EXEC_TO_DONE_LATENCY is required to
add those bars.

Per workload (simpoint-weighted averages A_no, A_config):
  reduction_pct = 100 * (A_no - A_config) / A_no

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_load_data_wait.py \
  --baseline-dir /users/deepmish/scarab/src/simulations/baseline \
  --rfp-dir /users/deepmish/scarab/src/simulations/rfp \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/load_data_wait
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_BASELINE_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_RFP_CONFIG,
    DEFAULT_RFP_DIR,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    DEFAULT_WORKLOADS_DB,
    FONT_FAMILY,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_FIGSIZE,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
    SIMPOINT_WORKLOADS,
    _apply_ipc_plot_style,
    _draw_app_x_tick_guides,
    _tight_x_limits,
    check_simpoint_coverage,
    find_simpoint_dir,
    grouped_x_positions,
    load_simpoint_trace_weights,
    register_noto_serif,
    rename_workload,
)

CORE_STAT_FILE = "core.stat.0.csv"
RFP_STAT_FILE = "rfp.stat.0.csv"
IFUSE_STAT_FILE = "ifuse.stat.0.csv"

# (stat_file, data-wait numerator, load-count denominator)
BASELINE_SOURCE = (CORE_STAT_FILE, "LD_EXEC_TO_DONE_LATENCY_count", "LD_RETIRED_ONPATH_count")
RFP_SOURCE = (RFP_STAT_FILE, "RFP_LD_EXEC_TO_DONE_count", "RFP_RETIRE_LOAD_count")
IFUSE_SOURCE = (IFUSE_STAT_FILE, "IFUSE_LD_EXEC_TO_DONE_count", "IFUSE_ALL_LOADS_count")

DATA_WAIT_SERIES: tuple[tuple[str, str, str], ...] = (
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
)
DATA_WAIT_BAR_WIDTH = 0.50
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "load_data_wait"


@dataclass
class DataWaitResult:
    workload: str
    baseline_data_wait: float
    rfp_data_wait: float
    ifuse_data_wait: float
    rfp_reduction_pct: float
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


def simpoint_data_wait(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    source: tuple[str, str, str],
    *,
    suite: str,
    subsuite: str,
) -> tuple[float, float] | None:
    """Return (data_wait_cycles, load_count) for one simpoint, or None if unavailable."""
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None
    stat_file, wait_stat, count_stat = source
    total = stat_count_from_csv(sim_dir / stat_file, wait_stat)
    loads = stat_count_from_csv(sim_dir / stat_file, count_stat)
    if total is None or loads is None or loads <= 0:
        return None
    return total, loads


def _reduction_pct(baseline_avg: float, config_avg: float) -> float:
    return 100.0 * (baseline_avg - config_avg) / baseline_avg


def compute_workload_data_wait(
    workload: str,
    baseline_dir: Path,
    rfp_dir: Path,
    ifuse_dir: Path,
    reference_traces: set[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    baseline_config: str,
    rfp_config: str,
    ifuse_config: str,
    suite: str,
    subsuite: str,
    load_count_tolerance: float,
) -> DataWaitResult | None:
    weighted_baseline = 0.0
    weighted_rfp = 0.0
    weighted_ifuse = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0 or cluster_id not in reference_traces:
            continue

        base = simpoint_data_wait(
            baseline_dir, baseline_config, workload, cluster_id, BASELINE_SOURCE,
            suite=suite, subsuite=subsuite,
        )
        rfp = simpoint_data_wait(
            rfp_dir, rfp_config, workload, cluster_id, RFP_SOURCE,
            suite=suite, subsuite=subsuite,
        )
        ifuse = simpoint_data_wait(
            ifuse_dir, ifuse_config, workload, cluster_id, IFUSE_SOURCE,
            suite=suite, subsuite=subsuite,
        )
        if base is None or rfp is None or ifuse is None:
            continue

        # All three counters must cover the same pre-fusion load population, otherwise
        # the averages are not comparable (this is the failure mode that corrupts the
        # exec-minus-fetch comparison against the ideal-fusion build).
        counts = [base[1], rfp[1], ifuse[1]]
        if max(counts) - min(counts) > load_count_tolerance * max(counts):
            print(
                f"  skip {workload}/{cluster_id}: load populations differ "
                f"(baseline={base[1]:.0f}, rfp={rfp[1]:.0f}, ifuse={ifuse[1]:.0f})"
            )
            continue

        weighted_baseline += weight * (base[0] / base[1])
        weighted_rfp += weight * (rfp[0] / rfp[1])
        weighted_ifuse += weight * (ifuse[0] / ifuse[1])
        weight_sum += weight
        trace_count += 1

    if weight_sum <= 0 or trace_count == 0:
        return None

    baseline_avg = weighted_baseline / weight_sum
    rfp_avg = weighted_rfp / weight_sum
    ifuse_avg = weighted_ifuse / weight_sum
    if baseline_avg <= 0:
        return None

    return DataWaitResult(
        workload=workload,
        baseline_data_wait=baseline_avg,
        rfp_data_wait=rfp_avg,
        ifuse_data_wait=ifuse_avg,
        rfp_reduction_pct=_reduction_pct(baseline_avg, rfp_avg),
        ifuse_reduction_pct=_reduction_pct(baseline_avg, ifuse_avg),
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[DataWaitResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "display_name",
                "trace_count",
                "baseline_data_wait_cycles",
                "rfp_data_wait_cycles",
                "rfp_reduction_pct",
                "ifuse_data_wait_cycles",
                "ifuse_reduction_pct",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.workload,
                    rename_workload(r.workload),
                    r.trace_count,
                    f"{r.baseline_data_wait:.3f}",
                    f"{r.rfp_data_wait:.3f}",
                    f"{r.rfp_reduction_pct:.2f}",
                    f"{r.ifuse_data_wait:.3f}",
                    f"{r.ifuse_reduction_pct:.2f}",
                ]
            )


def write_computation_log(path: Path, results: list[DataWaitResult]) -> None:
    with path.open("w") as fh:
        fh.write("Load data-wait latency (done_cycle - exec_cycle) per original load\n")
        fh.write("=" * 80 + "\n")
        fh.write("  no-fusion: LD_EXEC_TO_DONE_LATENCY / LD_RETIRED_ONPATH (core.stat.0.csv)\n")
        fh.write("  RFP:       RFP_LD_EXEC_TO_DONE     / RFP_RETIRE_LOAD   (rfp.stat.0.csv)\n")
        fh.write("  I-Fuse:    IFUSE_LD_EXEC_TO_DONE   / IFUSE_ALL_LOADS   (ifuse.stat.0.csv)\n")
        fh.write("Helios and ideal fusion omitted: those builds emit no exec-to-done counter.\n")
        fh.write("reduction_pct = 100 * (baseline - config) / baseline\n\n")
        for r in results:
            fh.write(f"{r.workload} ({rename_workload(r.workload)})\n")
            fh.write(f"  simpoints: {r.trace_count}\n")
            fh.write(f"  no-fusion data wait: {r.baseline_data_wait:.2f} cycles/load\n")
            fh.write(
                f"  RFP data wait:       {r.rfp_data_wait:.2f} cycles/load  "
                f"({r.rfp_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  I-Fuse data wait:    {r.ifuse_data_wait:.2f} cycles/load  "
                f"({r.ifuse_reduction_pct:.2f}% reduction)\n\n"
            )
        if results:
            rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean RFP reduction:    {rfp_avg:.2f}%\n")
            fh.write(f"Arithmetic mean I-Fuse reduction: {ifuse_avg:.2f}%\n")


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * DATA_WAIT_BAR_WIDTH for i in range(n)]


def plot_data_wait_bars(results: list[DataWaitResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    active_series: list[tuple[str, list[float], str]] = []
    for key, label, color in DATA_WAIT_SERIES:
        attr = {"rfp": "rfp_reduction_pct", "ifuse": "ifuse_reduction_pct"}[key]
        values = [float(getattr(r, attr)) for r in results]
        values.append(sum(values) / len(values))
        active_series.append((label, values, color))

    ordered_workloads = [r.workload for r in results]
    _ordered, x_map, avg_x, separator_x = grouped_x_positions(
        ordered_workloads, n_series=len(active_series)
    )
    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = [x_map[r.workload] for r in results] + [avg_x]
    offsets = _bar_offsets(len(active_series))

    _apply_ipc_plot_style()
    fig_w, fig_h = IPC_FIGSIZE
    fig, ax = plt.subplots(figsize=(fig_w, fig_h + 1.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    # Negative values (a regression vs no-fusion) are drawn below the axis in red outline
    # rather than clamped, so a reviewer sees them.
    for offset, (_label, values, color) in zip(offsets, active_series):
        edge_colors = ["red" if v < 0 else "black" for v in values]
        ax.bar(
            [i + offset for i in x],
            values,
            DATA_WAIT_BAR_WIDTH,
            color=color,
            edgecolor=edge_colors,
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )

    ax.axhline(y=0.0, color="black", linewidth=2.0, zorder=4)

    if len(display_apps) > 1:
        ax.axvline(
            x=separator_x,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            alpha=1.0,
            linewidth=AVERAGE_SEPARATOR_WIDTH,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        display_apps,
        rotation=45,
        ha="right",
        fontsize=IPC_TICK_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for i, label in enumerate(ax.get_xticklabels()):
        label.set_fontfamily(FONT_FAMILY)
        if i == len(display_apps) - 1:
            label.set_fontweight("bold")

    ax.set_ylabel(
        "Load data-wait\nreduction (%)\n(normalized to no-fusion)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )

    all_values = [v for _l, values, _c in active_series for v in values]
    ymax = max(all_values + [0.0])
    ymin = min(all_values + [0.0])
    ax.set_ylim(ymin * 1.25 - 2.0 if ymin < 0 else 0.0, ymax * 1.12 + 2.0)
    _tight_x_limits(ax, x[0], x[-1], n_bars=len(active_series))
    # The shared speedup y-axis helpers assume a positive-only range; this metric is
    # signed, so use an explicit locator instead.
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)
    _draw_app_x_tick_guides(ax, x)

    legend = ax.legend(
        handles=[
            plt.Rectangle(
                (0, 0), 1, 1,
                facecolor=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                label=label,
            )
            for _key, label, color in DATA_WAIT_SERIES
        ],
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        bbox_transform=ax.transAxes,
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        ncol=len(active_series),
        handlelength=0.95,
        handleheight=0.95,
        borderpad=0.55,
        labelspacing=0.4,
        columnspacing=1.0,
        framealpha=1.0,
    )
    legend.set_clip_on(False)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    plt.subplots_adjust(top=0.78, bottom=0.32, left=0.18, right=0.98)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("load_data_wait",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted load data-wait (done - exec) latency reduction "
            "for RFP and I-Fuse vs the no-fusion baseline."
        )
    )
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--workloads-db", type=Path, default=DEFAULT_WORKLOADS_DB)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--load-count-tolerance",
        type=float,
        default=0.001,
        help="Max relative disagreement between per-config load populations (default: %(default)s)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    baseline_dir = args.baseline_dir or DEFAULT_BASELINE_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(
        args.trace_root, workloads, workloads_db=args.workloads_db
    )

    print("Computing load data-wait (done - exec) reductions...")
    print(f"  baseline: {baseline_dir} (config={args.baseline_config})")
    print(f"  rfp:      {rfp_dir} (config={args.rfp_config})")
    print(f"  ifuse:    {ifuse_dir} (config={args.ifuse_config})")
    print("  helios / ideal fusion: omitted (no exec-to-done counter in those builds)")
    print(f"  output:   {output_dir}")

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "rfp": (rfp_dir, args.rfp_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
    }
    complete_apps, reference_by_workload = check_simpoint_coverage(
        directories,
        workloads,
        sp_weights,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
        report_path=output_dir / "load_data_wait_simpoint_coverage_report.txt",
    )
    if not complete_apps:
        raise SystemExit("No apps have complete simpoint files across baseline, rfp, and ifuse.")

    results: list[DataWaitResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_data_wait(
            workload,
            baseline_dir,
            rfp_dir,
            ifuse_dir,
            reference_by_workload[workload],
            sp_weights,
            baseline_config=args.baseline_config,
            rfp_config=args.rfp_config,
            ifuse_config=args.ifuse_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
            load_count_tolerance=args.load_count_tolerance,
        )
        if result is None:
            print(f"  skip {workload}: missing data-wait stats")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  base={result.baseline_data_wait:6.2f}  "
            f"rfp={result.rfp_reduction_pct:6.2f}%  ifuse={result.ifuse_reduction_pct:6.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete data-wait stats.")

    write_summary_csv(output_dir / "load_data_wait_summary.csv", results)
    write_computation_log(output_dir / "load_data_wait_computation_log.txt", results)
    plot_data_wait_bars(results, output_dir)

    rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  RFP mean data-wait reduction:    {rfp_avg:.2f}%")
    print(f"  I-Fuse mean data-wait reduction: {ifuse_avg:.2f}%")
    print("\nOutputs:")
    for suffix in ("png", "pdf", "eps"):
        print(f"  - {output_dir / f'load_data_wait.{suffix}'}")
    print(f"  - {output_dir / 'load_data_wait_summary.csv'}")
    print(f"  - {output_dir / 'load_data_wait_computation_log.txt'}")


if __name__ == "__main__":
    main()
