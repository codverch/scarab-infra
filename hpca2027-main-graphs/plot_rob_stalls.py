#!/usr/bin/env python3
"""Simpoint-weighted ROB stall reduction for I-Fuse and ideal fusion vs baseline.

Total ROB stalls per simpoint are the sum of all INST_LOST_ROB_STALL_* categories
from fetch.stat.0.csv:
  INST_LOST_ROB_STALL_OTHER_count
  INST_LOST_ROB_STALL_WAIT_FOR_RECOVERY_count
  INST_LOST_ROB_STALL_WAIT_FOR_REDIRECT_count
  INST_LOST_ROB_STALL_WAIT_FOR_GAP_FILL_count
  INST_LOST_ROB_STALL_WAIT_FOR_L1_MISS_count
  INST_LOST_ROB_STALL_WAIT_FOR_MEMORY_count
  INST_LOST_ROB_STALL_WAIT_FOR_DC_MISS_count

Per workload:
  reduction_pct = 100 * (weighted_baseline_stalls - weighted_config_stalls)
                  / weighted_baseline_stalls

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_rob_stalls.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab-infra/hpca2027-main-graphs/output
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
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IFUSE_CONFIG,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IDEAL_FUSION_COLOR,
    IFUSE_COLOR,
    MAROON_COLOR,
    SIMPOINT_WORKLOADS,
    check_simpoint_coverage,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

FETCH_STAT_FILE = "fetch.stat.0.csv"
ROB_STALL_STATS = (
    "INST_LOST_ROB_STALL_OTHER_count",
    "INST_LOST_ROB_STALL_WAIT_FOR_RECOVERY_count",
    "INST_LOST_ROB_STALL_WAIT_FOR_REDIRECT_count",
    "INST_LOST_ROB_STALL_WAIT_FOR_GAP_FILL_count",
    "INST_LOST_ROB_STALL_WAIT_FOR_L1_MISS_count",
    "INST_LOST_ROB_STALL_WAIT_FOR_MEMORY_count",
    "INST_LOST_ROB_STALL_WAIT_FOR_DC_MISS_count",
)


@dataclass
class RobStallResult:
    workload: str
    baseline_stalls: float
    ifuse_stalls: float
    ideal_stalls: float
    ifuse_reduction_pct: float
    ideal_reduction_pct: float
    trace_count: int


def stat_count_from_csv(stat_csv: Path, stat_name: str) -> float | None:
    if not stat_csv.is_file():
        return None
    total = 0.0
    found = False
    with stat_csv.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            if row[0].strip() == stat_name:
                try:
                    total += float(row[2].strip())
                    found = True
                except ValueError:
                    return None
    return total if found else None


def total_rob_stalls_from_csv(stat_csv: Path) -> float | None:
    total = 0.0
    for stat_name in ROB_STALL_STATS:
        value = stat_count_from_csv(stat_csv, stat_name)
        if value is None:
            return None
        total += value
    return total


def simpoint_rob_stalls(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> float | None:
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None
    return total_rob_stalls_from_csv(sim_dir / FETCH_STAT_FILE)


def compute_workload_stalls(
    workload: str,
    baseline_dir: Path,
    ifuse_dir: Path,
    ideal_dir: Path,
    reference_traces: set[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    baseline_config: str,
    ifuse_config: str,
    ideal_config: str,
    suite: str,
    subsuite: str,
) -> RobStallResult | None:
    weighted_baseline = 0.0
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0 or cluster_id not in reference_traces:
            continue

        baseline_val = simpoint_rob_stalls(
            baseline_dir,
            baseline_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        ifuse_val = simpoint_rob_stalls(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_val = simpoint_rob_stalls(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        if baseline_val is None or ifuse_val is None or ideal_val is None:
            continue

        weighted_baseline += weight * baseline_val
        weighted_ifuse += weight * ifuse_val
        weighted_ideal += weight * ideal_val
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_baseline <= 0:
        return None

    return RobStallResult(
        workload=workload,
        baseline_stalls=weighted_baseline,
        ifuse_stalls=weighted_ifuse,
        ideal_stalls=weighted_ideal,
        ifuse_reduction_pct=100.0 * (weighted_baseline - weighted_ifuse) / weighted_baseline,
        ideal_reduction_pct=100.0 * (weighted_baseline - weighted_ideal) / weighted_baseline,
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[RobStallResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "weighted_baseline_stalls",
                "weighted_ifuse_stalls",
                "weighted_ideal_stalls",
                "ifuse_reduction_pct",
                "ideal_reduction_pct",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "weighted_baseline_stalls": f"{result.baseline_stalls:.1f}",
                    "weighted_ifuse_stalls": f"{result.ifuse_stalls:.1f}",
                    "weighted_ideal_stalls": f"{result.ideal_stalls:.1f}",
                    "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                    "ideal_reduction_pct": f"{result.ideal_reduction_pct:.2f}",
                }
            )


def write_computation_log(path: Path, results: list[RobStallResult]) -> None:
    with path.open("w") as fh:
        fh.write("ROB stall reduction (sum of INST_LOST_ROB_STALL_* from fetch.stat.0.csv)\n")
        fh.write("=" * 80 + "\n")
        for stat_name in ROB_STALL_STATS:
            fh.write(f"  + {stat_name}\n")
        fh.write(
            "\nreduction_pct = 100 * (weighted_baseline - weighted_config) / weighted_baseline\n"
        )
        fh.write("All configurations read fetch.stat.0.csv.\n\n")
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted baseline stalls: {result.baseline_stalls:.1f}\n")
            fh.write(
                f"  weighted ifuse stalls:    {result.ifuse_stalls:.1f}  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted ideal stalls:    {result.ideal_stalls:.1f}  "
                f"({result.ideal_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse reduction:  {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal reduction:   {ideal_avg:.2f}%\n")


def plot_rob_stall_bars(results: list[RobStallResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt

    ifuse_pct = [r.ifuse_reduction_pct for r in results]
    ideal_pct = [r.ideal_reduction_pct for r in results]

    ifuse_mean = sum(ifuse_pct) / len(ifuse_pct)
    ideal_mean = sum(ideal_pct) / len(ideal_pct)
    ifuse_pct.append(ifuse_mean)
    ideal_pct.append(ideal_mean)

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    width = 0.18

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ifuse_edge_colors = ["red" if val < 0 else "black" for val in ifuse_pct]
    ifuse_edge_widths = [2.5 if val < 0 else 1.0 for val in ifuse_pct]
    ideal_edge_colors = ["red" if val < 0 else "black" for val in ideal_pct]
    ideal_edge_widths = [2.5 if val < 0 else 1.0 for val in ideal_pct]

    ax.bar(
        [i - 0.5 * width for i in x],
        ifuse_pct,
        width,
        label="I-Fuse",
        color=IFUSE_COLOR,
        edgecolor=ifuse_edge_colors,
        linewidth=ifuse_edge_widths,
        zorder=3,
    )
    ax.bar(
        [i + 0.5 * width for i in x],
        ideal_pct,
        width,
        label="Ideal fusion",
        color=IDEAL_FUSION_COLOR,
        edgecolor=ideal_edge_colors,
        linewidth=ideal_edge_widths,
        zorder=3,
    )

    offsets = [-0.5 * width, 0.5 * width]
    colors = [IFUSE_COLOR, IDEAL_FUSION_COLOR]
    pct_sets = [ifuse_pct, ideal_pct]
    for i in range(len(display_apps)):
        for bar_offset, color, pct_list in zip(offsets, colors, pct_sets):
            val = pct_list[i]
            if 0 <= val < ARROW_THRESHOLD:
                ax.annotate(
                    "",
                    xy=(i + bar_offset, 0),
                    xytext=(i + bar_offset, 5.5),
                    arrowprops=dict(
                        arrowstyle="->", color=color, lw=1.5, mutation_scale=12
                    ),
                    zorder=10,
                )
                ax.text(
                    i + bar_offset - 0.12,
                    5.5,
                    f"{val:.1f}",
                    ha="center",
                    va="bottom",
                    fontsize=24,
                    fontfamily="serif",
                    color=color,
                    zorder=10,
                )

    if len(display_apps) > 1:
        separator_x = len(display_apps) - 1.5
        ax.axvline(
            x=separator_x,
            color=MAROON_COLOR,
            linestyle="--",
            alpha=0.8,
            linewidth=2.5,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(display_apps, rotation=45, ha="right", fontsize=26, fontfamily="serif")
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    ax.set_ylabel(
        "ROB stalls reduction (%)\n(normalized to no-fusion baseline)",
        fontsize=26,
        fontfamily="serif",
    )
    ymin = min(0.0, min(ifuse_pct + ideal_pct))
    ax.set_ylim(ymin, 100.0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="y", labelsize=20)
    for label in ax.get_yticklabels():
        label.set_fontfamily("serif")

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

    plt.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("rob_stalls",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted ROB stall reduction for I-Fuse and "
            "ideal fusion vs baseline."
        )
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--ifuse-config", default=DEFAULT_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: hpca2027-main-graphs/output)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or (sim_root / "baseline")
    ifuse_dir = args.ifuse_dir or (sim_root / "ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion")
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or (GRAPH_DIR / "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing ROB stall reductions (INST_LOST_ROB_STALL_* from fetch.stat.0.csv)...")
    print(f"  baseline:     {baseline_dir} (config={args.baseline_config})")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config),
    }
    complete_apps, reference_by_workload = check_simpoint_coverage(
        directories,
        workloads,
        sp_weights,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
        report_path=output_dir / "rob_stalls_simpoint_coverage_report.txt",
    )
    if not complete_apps:
        raise SystemExit(
            "No apps have complete simpoint files across baseline, ifuse, and ideal fusion."
        )

    results: list[RobStallResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_stalls(
            workload,
            baseline_dir,
            ifuse_dir,
            ideal_dir,
            reference_by_workload[workload],
            sp_weights,
            baseline_config=args.baseline_config,
            ifuse_config=args.ifuse_config,
            ideal_config=args.ideal_fusion_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing ROB stall stats in fetch.stat.0.csv")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  baseline={result.baseline_stalls:,.0f}  "
            f"ifuse={result.ifuse_reduction_pct:5.2f}%  "
            f"ideal={result.ideal_reduction_pct:5.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete ROB stall data.")

    write_summary_csv(output_dir / "rob_stalls_summary.csv", results)
    write_computation_log(output_dir / "rob_stalls_computation_log.txt", results)
    plot_rob_stall_bars(results, output_dir)

    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  I-Fuse mean reduction:       {ifuse_avg:.2f}%")
    print(f"  Ideal fusion mean reduction: {ideal_avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'rob_stalls.png'}")
    print(f"  - {output_dir / 'rob_stalls.pdf'}")
    print(f"  - {output_dir / 'rob_stalls.eps'}")
    print(f"  - {output_dir / 'rob_stalls_summary.csv'}")
    print(f"  - {output_dir / 'rob_stalls_computation_log.txt'}")


if __name__ == "__main__":
    main()
