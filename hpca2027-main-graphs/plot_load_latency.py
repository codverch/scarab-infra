#!/usr/bin/env python3
"""Simpoint-weighted load latency reduction for I-Fuse and ideal fusion vs baseline.

Uses the total summed exec-minus-fetch latency per simpoint:
  LD_EXEC_MINUS_FETCH_LATENCY_count

Per workload (simpoint-weighted):
  reduction_pct = 100 * (weighted_baseline_total - weighted_config_total)
                  / weighted_baseline_total

Equivalently: 100 * (1 - ifuse_total / baseline_total)

Baseline and I-Fuse read core.stat.0.csv. Ideal fusion reads ideal_fusion.stat.0.csv;
if the stored exec total is corrupt (>1e12, fused LOAD2 ops with exec_cycle=MAX_CTR),
it is estimated as LD_RETIRE * (baseline_exec / baseline_retire) for that simpoint.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_load_latency.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/load_latency

cd /users/deepmish/scarab
git add src/hpca2027-main-graphs-results/load_latency/
git commit -m "Update HPCA main-graph load latency results."
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
    DEFAULT_LOAD_LATENCY_OUTPUT_DIR,
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

LOAD_EXEC_LATENCY_STAT = "LD_EXEC_MINUS_FETCH_LATENCY_count"
LOAD_RETIRE_LATENCY_STAT = "LD_RETIRE_MINUS_FETCH_LATENCY_count"
CORE_STAT_FILE = "core.stat.0.csv"
IDEAL_STAT_FILE = "ideal_fusion.stat.0.csv"
MAX_SANE_EXEC_TOTAL = 1e12


@dataclass
class LoadLatencyResult:
    workload: str
    baseline_exec_latency: float
    ifuse_exec_latency: float
    ideal_exec_latency: float
    ifuse_reduction_pct: float
    ideal_reduction_pct: float
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


def ideal_exec_latency_total(
    ideal_csv: Path,
    *,
    baseline_exec: float,
    baseline_retire: float,
) -> float | None:
    exec_total = stat_count_from_csv(ideal_csv, LOAD_EXEC_LATENCY_STAT)
    if exec_total is None:
        return None
    if exec_total > MAX_SANE_EXEC_TOTAL:
        retire_total = stat_count_from_csv(ideal_csv, LOAD_RETIRE_LATENCY_STAT)
        if (
            retire_total is None
            or baseline_retire <= 0
            or baseline_exec <= 0
        ):
            return None
        exec_total = retire_total * (baseline_exec / baseline_retire)
    return exec_total


def simpoint_exec_latency_total(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    use_ideal_stat: bool,
    baseline_exec: float | None = None,
    baseline_retire: float | None = None,
    suite: str,
    subsuite: str,
) -> float | None:
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None

    if use_ideal_stat:
        ideal_csv = sim_dir / IDEAL_STAT_FILE
        if baseline_exec is None or baseline_retire is None:
            return None
        return ideal_exec_latency_total(
            ideal_csv,
            baseline_exec=baseline_exec,
            baseline_retire=baseline_retire,
        )

    return stat_count_from_csv(sim_dir / CORE_STAT_FILE, LOAD_EXEC_LATENCY_STAT)


def simpoint_baseline_exec_retire(
    baseline_dir: Path,
    baseline_config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> tuple[float | None, float | None]:
    sim_dir = find_simpoint_dir(
        baseline_dir, baseline_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None, None
    core_csv = sim_dir / CORE_STAT_FILE
    exec_total = stat_count_from_csv(core_csv, LOAD_EXEC_LATENCY_STAT)
    retire_total = stat_count_from_csv(core_csv, LOAD_RETIRE_LATENCY_STAT)
    return exec_total, retire_total


def compute_workload_latency(
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
) -> LoadLatencyResult | None:
    weighted_baseline = 0.0
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0 or cluster_id not in reference_traces:
            continue

        baseline_exec, baseline_retire = simpoint_baseline_exec_retire(
            baseline_dir,
            baseline_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        baseline_val = simpoint_exec_latency_total(
            baseline_dir,
            baseline_config,
            workload,
            cluster_id,
            use_ideal_stat=False,
            suite=suite,
            subsuite=subsuite,
        )
        ifuse_val = simpoint_exec_latency_total(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            use_ideal_stat=False,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_val = simpoint_exec_latency_total(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            use_ideal_stat=True,
            baseline_exec=baseline_exec,
            baseline_retire=baseline_retire,
            suite=suite,
            subsuite=subsuite,
        )
        if (
            baseline_val is None
            or ifuse_val is None
            or ideal_val is None
            or baseline_exec is None
            or baseline_retire is None
        ):
            continue

        weighted_baseline += weight * baseline_val
        weighted_ifuse += weight * ifuse_val
        weighted_ideal += weight * ideal_val
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_baseline <= 0:
        return None

    return LoadLatencyResult(
        workload=workload,
        baseline_exec_latency=weighted_baseline,
        ifuse_exec_latency=weighted_ifuse,
        ideal_exec_latency=weighted_ideal,
        ifuse_reduction_pct=100.0 * (weighted_baseline - weighted_ifuse) / weighted_baseline,
        ideal_reduction_pct=100.0 * (weighted_baseline - weighted_ideal) / weighted_baseline,
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[LoadLatencyResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "weighted_baseline_exec_latency",
                "weighted_ifuse_exec_latency",
                "weighted_ideal_exec_latency",
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
                    "weighted_baseline_exec_latency": f"{result.baseline_exec_latency:.1f}",
                    "weighted_ifuse_exec_latency": f"{result.ifuse_exec_latency:.1f}",
                    "weighted_ideal_exec_latency": f"{result.ideal_exec_latency:.1f}",
                    "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                    "ideal_reduction_pct": f"{result.ideal_reduction_pct:.2f}",
                }
            )


def write_computation_log(path: Path, results: list[LoadLatencyResult]) -> None:
    with path.open("w") as fh:
        fh.write("Load latency reduction (LD_EXEC_MINUS_FETCH_LATENCY_count)\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "reduction_pct = 100 * (weighted_baseline_total - weighted_config_total) "
            "/ weighted_baseline_total\n"
        )
        fh.write(
            "equivalently: 100 * (1 - weighted_ifuse_total / weighted_baseline_total)\n"
        )
        fh.write(
            "LD_EXEC_MINUS_FETCH_LATENCY: baseline/I-Fuse from core.stat.0.csv; "
            "ideal fusion from ideal_fusion.stat.0.csv\n"
        )
        fh.write(
            "Ideal fusion exec totals above 1e12 are corrected using "
            "LD_RETIRE * (baseline_exec / baseline_retire) per simpoint.\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(
                f"  weighted baseline exec-fetch total: {result.baseline_exec_latency:.1f}\n"
            )
            fh.write(
                f"  weighted ifuse exec-fetch total:   {result.ifuse_exec_latency:.1f}  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted ideal exec-fetch total:   {result.ideal_exec_latency:.1f}  "
                f"({result.ideal_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse reduction:  {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal reduction:   {ideal_avg:.2f}%\n")


def plot_load_latency_reduction_bars(results: list[LoadLatencyResult], output_dir: Path) -> None:
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
        "Load latency reduction (%)\n(normalized to no-fusion baseline)",
        fontsize=26,
        fontfamily="serif",
    )
    ymax = max(ifuse_pct + ideal_pct)
    ymin = min(0.0, min(ifuse_pct + ideal_pct))
    ax.set_ylim(ymin, ymax * 1.12 + 2.0)
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
    for stem in ("load_latency",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted load latency reduction for I-Fuse and "
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
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/load_latency)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or (sim_root / "baseline")
    ifuse_dir = args.ifuse_dir or (sim_root / "ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion")
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_LOAD_LATENCY_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing load latency reductions (LD_EXEC_MINUS_FETCH_LATENCY)...")
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
        report_path=output_dir / "load_latency_simpoint_coverage_report.txt",
    )
    if not complete_apps:
        raise SystemExit(
            "No apps have complete simpoint files across baseline, ifuse, and ideal fusion."
        )

    results: list[LoadLatencyResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_latency(
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
            print(f"  skip {workload}: missing load latency stats")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  baseline={result.baseline_exec_latency:,.0f}  "
            f"ifuse={result.ifuse_reduction_pct:5.2f}%  "
            f"ideal={result.ideal_reduction_pct:5.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete load latency data.")

    write_summary_csv(output_dir / "load_latency_summary.csv", results)
    write_computation_log(output_dir / "load_latency_computation_log.txt", results)
    plot_load_latency_reduction_bars(results, output_dir)

    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  I-Fuse mean reduction:       {ifuse_avg:.2f}%")
    print(f"  Ideal fusion mean reduction: {ideal_avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'load_latency.png'}")
    print(f"  - {output_dir / 'load_latency.pdf'}")
    print(f"  - {output_dir / 'load_latency.eps'}")
    print(f"  - {output_dir / 'load_latency_summary.csv'}")
    print(f"  - {output_dir / 'load_latency_computation_log.txt'}")


if __name__ == "__main__":
    main()
