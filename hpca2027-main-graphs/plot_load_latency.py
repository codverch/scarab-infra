#!/usr/bin/env python3
"""Simpoint-weighted total load exec-fetch latency reduction for I-Fuse vs no-fusion.

Measures average fetch-to-execute latency across all on-path loads:

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
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_LOAD_LATENCY_OUTPUT_DIR,
    DEFAULT_RFP_CONFIG,
    DEFAULT_RFP_BASELINE_DIR,
    DEFAULT_RFP_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    HELIOS_COLOR,
    IDEAL_FUSION_COLOR,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
    SIMPOINT_WORKLOADS,
    _apply_speedup_y_grid,
    _apply_speedup_y_ticks,
    _draw_app_x_tick_guides,
    _tight_x_limits,
    check_simpoint_coverage,
    find_simpoint_dir,
    grouped_x_positions,
    load_simpoint_trace_weights,
    rename_workload,
)

LOAD_LATENCY_STAT = "LD_EXEC_MINUS_FETCH_LATENCY_count"
IDEAL_LOAD_LATENCY_STAT = "LD_RETIRE_MINUS_FETCH_LATENCY_count"
IFUSE_ALL_LOADS_STAT = "IFUSE_ALL_LOADS_count"
ONPATH_MEM_LOADS_STAT = "ONPATH_MEM_LOADS_count"

CORE_STAT_FILE = "core.stat.0.csv"
IFUSE_STAT_FILE = "ifuse.stat.0.csv"
IDEAL_STAT_FILE = "ideal_fusion.stat.0.csv"

LATENCY_SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
)
LATENCY_BAR_WIDTH = 0.36


@dataclass
class LoadLatencyResult:
    workload: str
    baseline_load_latency: float
    helios_load_latency: float | None
    rfp_load_latency: float
    ifuse_load_latency: float
    ideal_load_latency: float
    helios_reduction_pct: float | None
    rfp_reduction_pct: float
    ifuse_reduction_pct: float
    ideal_reduction_pct: float
    trace_count: int
    helios_trace_count: int


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


def simpoint_load_count(
    ifuse_dir: Path,
    ideal_dir: Path,
    ifuse_config: str,
    ideal_config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> float | None:
    ifuse_sim = find_simpoint_dir(
        ifuse_dir, ifuse_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    ideal_sim = find_simpoint_dir(
        ideal_dir, ideal_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    load_count = None
    if ideal_sim is not None:
        load_count = stat_count_from_csv(ideal_sim / IDEAL_STAT_FILE, ONPATH_MEM_LOADS_STAT)
    if (load_count is None or load_count <= 0) and ifuse_sim is not None:
        load_count = stat_count_from_csv(ifuse_sim / IFUSE_STAT_FILE, IFUSE_ALL_LOADS_STAT)
    if load_count is None or load_count <= 0:
        return None
    return load_count


def simpoint_scheme_load_latency(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    stat_file: str,
    suite: str,
    subsuite: str,
) -> float | None:
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None
    stat_name = IDEAL_LOAD_LATENCY_STAT if stat_file == IDEAL_STAT_FILE else LOAD_LATENCY_STAT
    return stat_count_from_csv(sim_dir / stat_file, stat_name)


def _reduction_pct(baseline_avg: float, config_avg: float) -> float:
    return 100.0 * (baseline_avg - config_avg) / baseline_avg


def compute_workload_load_latency(
    workload: str,
    baseline_dir: Path,
    helios_dir: Path | None,
    rfp_dir: Path,
    ifuse_dir: Path,
    ideal_dir: Path,
    reference_traces: set[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    baseline_config: str,
    helios_config: str,
    rfp_config: str,
    ifuse_config: str,
    ideal_config: str,
    include_helios: bool,
    suite: str,
    subsuite: str,
) -> LoadLatencyResult | None:
    weighted_baseline = 0.0
    weighted_helios = 0.0
    weighted_helios_baseline = 0.0
    weighted_rfp = 0.0
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weight_sum = 0.0
    trace_count = 0
    helios_trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0 or cluster_id not in reference_traces:
            continue

        load_count = simpoint_load_count(
            ifuse_dir,
            ideal_dir,
            ifuse_config,
            ideal_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        if load_count is None:
            continue

        baseline_latency = simpoint_scheme_load_latency(
            baseline_dir,
            baseline_config,
            workload,
            cluster_id,
            stat_file=CORE_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        rfp_latency = simpoint_scheme_load_latency(
            rfp_dir,
            rfp_config,
            workload,
            cluster_id,
            stat_file=CORE_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        ifuse_latency = simpoint_scheme_load_latency(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file=CORE_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_latency = simpoint_scheme_load_latency(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file=IDEAL_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        if (
            baseline_latency is None
            or rfp_latency is None
            or ifuse_latency is None
            or ideal_latency is None
            or baseline_latency <= 0
            or rfp_latency <= 0
            or ifuse_latency <= 0
            or ideal_latency <= 0
        ):
            continue

        baseline_avg = baseline_latency / load_count
        rfp_avg = rfp_latency / load_count
        ifuse_avg = ifuse_latency / load_count
        ideal_avg = ideal_latency / load_count

        weighted_baseline += weight * baseline_avg
        weighted_rfp += weight * rfp_avg
        weighted_ifuse += weight * ifuse_avg
        weighted_ideal += weight * ideal_avg

        if include_helios and helios_dir is not None:
            helios_latency = simpoint_scheme_load_latency(
                helios_dir,
                helios_config,
                workload,
                cluster_id,
                stat_file=CORE_STAT_FILE,
                suite=suite,
                subsuite=subsuite,
            )
            if helios_latency is not None and helios_latency > 0:
                weighted_helios += weight * (helios_latency / load_count)
                weighted_helios_baseline += weight * baseline_avg
                helios_trace_count += 1

        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_baseline <= 0:
        return None

    baseline_load_latency = weighted_baseline / weight_sum
    helios_load_latency = (
        weighted_helios / weight_sum if include_helios and helios_trace_count > 0 else None
    )
    rfp_load_latency = weighted_rfp / weight_sum
    ifuse_load_latency = weighted_ifuse / weight_sum
    ideal_load_latency = weighted_ideal / weight_sum

    return LoadLatencyResult(
        workload=workload,
        baseline_load_latency=baseline_load_latency,
        helios_load_latency=helios_load_latency,
        rfp_load_latency=rfp_load_latency,
        ifuse_load_latency=ifuse_load_latency,
        ideal_load_latency=ideal_load_latency,
        helios_reduction_pct=(
            100.0 * (weighted_helios_baseline - weighted_helios) / weighted_helios_baseline
            if include_helios and helios_trace_count > 0 and weighted_helios_baseline > 0
            else None
        ),
        rfp_reduction_pct=_reduction_pct(baseline_load_latency, rfp_load_latency),
        ifuse_reduction_pct=_reduction_pct(baseline_load_latency, ifuse_load_latency),
        ideal_reduction_pct=_reduction_pct(baseline_load_latency, ideal_load_latency),
        trace_count=trace_count,
        helios_trace_count=helios_trace_count,
    )


def write_summary_csv(path: Path, results: list[LoadLatencyResult], *, include_helios: bool) -> None:
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_baseline_load_latency",
    ]
    if include_helios:
        fieldnames.extend(
            [
                "helios_trace_count",
                "weighted_helios_load_latency",
                "helios_reduction_pct",
            ]
        )
    fieldnames.extend(
        [
            "weighted_rfp_load_latency",
            "rfp_reduction_pct",
            "weighted_ifuse_load_latency",
            "ifuse_reduction_pct",
            "weighted_ideal_load_latency",
            "ideal_reduction_pct",
        ]
    )
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = {
                "workload": result.workload,
                "display_name": rename_workload(result.workload),
                "trace_count": result.trace_count,
                "weighted_baseline_load_latency": f"{result.baseline_load_latency:.2f}",
                "weighted_rfp_load_latency": f"{result.rfp_load_latency:.2f}",
                "rfp_reduction_pct": f"{result.rfp_reduction_pct:.2f}",
                "weighted_ifuse_load_latency": f"{result.ifuse_load_latency:.2f}",
                "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                "weighted_ideal_load_latency": f"{result.ideal_load_latency:.2f}",
                "ideal_reduction_pct": f"{result.ideal_reduction_pct:.2f}",
            }
            if include_helios:
                row["helios_trace_count"] = result.helios_trace_count
                row["weighted_helios_load_latency"] = (
                    f"{result.helios_load_latency:.2f}" if result.helios_load_latency is not None else ""
                )
                row["helios_reduction_pct"] = (
                    f"{result.helios_reduction_pct:.2f}" if result.helios_reduction_pct is not None else ""
                )
            writer.writerow(row)


def write_computation_log(
    path: Path, results: list[LoadLatencyResult], *, include_helios: bool
) -> None:
    with path.open("w") as fh:
        fh.write("Total load latency reduction vs no-fusion\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "Per simpoint:\n"
            "  baseline/rfp/ifuse: LD_EXEC_MINUS_FETCH_LATENCY / on-path-load-count\n"
            "  ideal fusion:       LD_EXEC_MINUS_FETCH_LATENCY / ONPATH_MEM_LOADS_count\n"
            "  on-path-load-count: ONPATH_MEM_LOADS_count from ideal_fusion.stat.0.csv "
            "(fallback: IFUSE_ALL_LOADS_count)\n"
            "reduction_pct = 100 * (weighted_baseline_load_latency - weighted_config_load_latency) "
            "/ weighted_baseline_load_latency\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(
                f"  weighted no-fusion load latency: {result.baseline_load_latency:.2f} cycles\n"
            )
            if include_helios and result.helios_load_latency is not None:
                fh.write(
                    f"  weighted Helios load latency:    {result.helios_load_latency:.2f} cycles  "
                    f"({result.helios_reduction_pct:.2f}% reduction)\n"
                )
            fh.write(
                f"  weighted RFP load latency:       {result.rfp_load_latency:.2f} cycles  "
                f"({result.rfp_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted I-Fuse load latency:    {result.ifuse_load_latency:.2f} cycles  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted Ideal load latency:     {result.ideal_load_latency:.2f} cycles  "
                f"({result.ideal_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            if include_helios:
                helios_vals = [r.helios_reduction_pct for r in results if r.helios_reduction_pct is not None]
                if helios_vals:
                    fh.write(f"Arithmetic mean Helios reduction: {sum(helios_vals) / len(helios_vals):.2f}%\n")
            rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean RFP reduction:    {rfp_avg:.2f}%\n")
            fh.write(f"Arithmetic mean I-Fuse reduction: {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal reduction:  {ideal_avg:.2f}%\n")


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * LATENCY_BAR_WIDTH for i in range(n)]


def plot_load_latency_reduction_bars(
    results: list[LoadLatencyResult], output_dir: Path, *, include_helios: bool
) -> None:
    import matplotlib.pyplot as plt

    active_series: list[tuple[str, list[float], str]] = []
    for key, label, color in LATENCY_SERIES:
        if key == "helios" and not include_helios:
            continue
        attr = {
            "helios": "helios_reduction_pct",
            "rfp": "rfp_reduction_pct",
            "ifuse": "ifuse_reduction_pct",
            "ideal": "ideal_reduction_pct",
        }[key]
        values = [getattr(result, attr) or 0.0 for result in results]
        values.append(sum(values) / len(values))
        active_series.append((label, values, color))

    ordered_workloads = [result.workload for result in results]
    _ordered, x_map, avg_x, separator_x = grouped_x_positions(
        ordered_workloads, n_series=len(active_series)
    )
    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = [x_map[result.workload] for result in results] + [avg_x]
    offsets = _bar_offsets(len(active_series))

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
    fig_width = max(22.0, len(x) * 1.15 + 1.15)
    fig, ax = plt.subplots(figsize=(fig_width, 6.5))

    for offset, (_label, values, color) in zip(offsets, active_series):
        ax.bar(
            [i + offset for i in x],
            [max(0.0, val) for val in values],
            LATENCY_BAR_WIDTH,
            color=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )

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
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    ax.set_ylabel(
        "Load latency reduction\ndue to speculation\n(normalized to no-fusion) (%)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )

    ymax = max(value for _label, values, _color in active_series for value in values)
    ax.set_ylim(0.0, ymax * 1.12 + 2.0)
    _tight_x_limits(ax, x[0], x[-1], n_bars=len(active_series))
    _apply_speedup_y_ticks(ax)
    _apply_speedup_y_grid(ax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)
    _draw_app_x_tick_guides(ax, x)

    fig.subplots_adjust(left=0.10, bottom=0.28, right=0.99, top=0.90)

    legend = ax.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, facecolor=color, edgecolor="black", linewidth=BAR_EDGE_WIDTH, label=label)
            for _key, label, color in LATENCY_SERIES
            if not (_key == "helios" and not include_helios)
        ],
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.96),
        bbox_transform=ax.transAxes,
        borderaxespad=0.0,
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        ncol=len(active_series),
        handlelength=1.4,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

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
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/load_latency)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    baseline_dir = args.baseline_dir or DEFAULT_RFP_BASELINE_DIR
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_LOAD_LATENCY_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing total load exec-fetch latency reductions...")
    print(f"  baseline: {baseline_dir} (config={args.baseline_config})")
    print(f"  helios:   {helios_dir} (config={args.helios_config})")
    print(f"  rfp:      {rfp_dir} (config={args.rfp_config})")
    print(f"  ifuse:    {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal:    {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:   {output_dir}")

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "rfp": (rfp_dir, args.rfp_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
        "ideal": (ideal_dir, args.ideal_fusion_config),
        "helios": (helios_dir, args.helios_config),
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
        raise SystemExit("No apps have complete simpoint files across the load-latency inputs.")

    results: list[LoadLatencyResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_load_latency(
            workload,
            baseline_dir,
            helios_dir,
            rfp_dir,
            ifuse_dir,
            ideal_dir,
            reference_by_workload[workload],
            sp_weights,
            baseline_config=args.baseline_config,
            helios_config=args.helios_config,
            rfp_config=args.rfp_config,
            ifuse_config=args.ifuse_config,
            ideal_config=args.ideal_fusion_config,
            include_helios=True,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing load latency stats")
            continue
        results.append(result)
        parts = [f"  {workload:14s}"]
        if result.helios_reduction_pct is not None:
            parts.append(f"helios={result.helios_reduction_pct:5.2f}%")
        parts.append(f"rfp={result.rfp_reduction_pct:5.2f}%")
        parts.append(f"ifuse={result.ifuse_reduction_pct:5.2f}%")
        parts.append(f"ideal={result.ideal_reduction_pct:5.2f}%")
        parts.append(f"(simpoints={result.trace_count})")
        print("  ".join(parts))

    if not results:
        raise SystemExit("No workloads with complete load latency data.")

    include_helios = any(result.helios_reduction_pct is not None for result in results)
    write_summary_csv(output_dir / "load_latency_summary.csv", results, include_helios=include_helios)
    write_computation_log(
        output_dir / "load_latency_computation_log.txt", results, include_helios=include_helios
    )
    plot_load_latency_reduction_bars(results, output_dir, include_helios=include_helios)

    if include_helios:
        helios_vals = [r.helios_reduction_pct for r in results if r.helios_reduction_pct is not None]
        if helios_vals:
            print(f"  Helios mean reduction: {sum(helios_vals) / len(helios_vals):.2f}%")
    rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  RFP mean reduction:    {rfp_avg:.2f}%")
    print(f"  I-Fuse mean reduction: {ifuse_avg:.2f}%")
    print(f"  Ideal mean reduction:  {ideal_avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'load_latency.png'}")
    print(f"  - {output_dir / 'load_latency.pdf'}")
    print(f"  - {output_dir / 'load_latency.eps'}")
    print(f"  - {output_dir / 'load_latency_summary.csv'}")
    print(f"  - {output_dir / 'load_latency_computation_log.txt'}")


if __name__ == "__main__":
    main()
