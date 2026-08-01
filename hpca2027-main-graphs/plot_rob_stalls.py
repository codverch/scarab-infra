#!/usr/bin/env python3
"""Simpoint-weighted ROB stall reduction for Helios, RFP, I-Fuse, and ideal fusion.

Total ROB stalls per simpoint are the sum of all INST_LOST_ROB_STALL_* categories
from fetch.stat.0.csv:
  INST_LOST_ROB_STALL_OTHER_count
  INST_LOST_ROB_STALL_WAIT_FOR_RECOVERY_count
  INST_LOST_ROB_STALL_WAIT_FOR_REDIRECT_count
  INST_LOST_ROB_STALL_WAIT_FOR_GAP_FILL_count
  INST_LOST_ROB_STALL_WAIT_FOR_L1_MISS_count
  INST_LOST_ROB_STALL_WAIT_FOR_MEMORY_count
  INST_LOST_ROB_STALL_WAIT_FOR_DC_MISS_count

All configurations read fetch.stat.0.csv. Baseline denominator uses baseline/.

Per workload:
  reduction_pct = 100 * (weighted_baseline_stalls - weighted_config_stalls)
                  / weighted_baseline_stalls

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_rob_stalls.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --baseline-dir /users/deepmish/scarab/src/simulations/baseline \
  --helios-dir /users/deepmish/scarab/src/simulations/helios \
  --rfp-dir /users/deepmish/scarab/src/simulations/rfp \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --ideal-fusion-dir /users/deepmish/scarab/src/simulations/ideal-fusion \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/rob_stalls
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
    APP_STEP,
    AVERAGE_GAP,
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    BAR_WIDTH,
    DEFAULT_BASELINE_DIR,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_ROB_STALLS_OUTPUT_DIR,
    DEFAULT_RFP_CONFIG,
    DEFAULT_RFP_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    DEFAULT_WORKLOADS_DB,
    FONT_FAMILY,
    HELIOS_COLOR,
    IDEAL_FUSION_COLOR,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_FIGSIZE,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
    SIMPOINT_WORKLOADS,
    _apply_ipc_plot_style,
    _bar_offsets,
    _draw_app_x_tick_guides,
    _tight_x_limits,
    check_simpoint_coverage,
    find_simpoint_dir,
    grouped_x_positions,
    load_simpoint_trace_weights,
    order_workloads_by_group,
    register_noto_serif,
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

SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ideal", "Ideal fusion", IDEAL_FUSION_COLOR),
)


@dataclass
class RobStallResult:
    workload: str
    baseline_stalls: float
    helios_stalls: float | None
    rfp_stalls: float
    ifuse_stalls: float
    ideal_stalls: float
    helios_reduction_pct: float | None
    rfp_reduction_pct: float
    ifuse_reduction_pct: float
    ideal_reduction_pct: float
    trace_count: int
    helios_trace_count: int
    rfp_trace_count: int


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


def helios_rob_stats_available(
    helios_dir: Path,
    helios_config: str,
    workloads: list[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    suite: str,
    subsuite: str,
) -> bool:
    for workload in workloads:
        for (wl, cluster_id), weight in sp_weights.items():
            if wl != workload or weight <= 0:
                continue
            sim_dir = find_simpoint_dir(
                helios_dir, helios_config, workload, cluster_id, suite=suite, subsuite=subsuite
            )
            if sim_dir is None:
                continue
            if (
                stat_count_from_csv(
                    sim_dir / FETCH_STAT_FILE, ROB_STALL_STATS[0]
                )
                is not None
            ):
                return True
    return False


def _reduction_pct(weighted_baseline: float, weighted_config: float) -> float:
    return 100.0 * (weighted_baseline - weighted_config) / weighted_baseline


def compute_workload_stalls(
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
) -> RobStallResult | None:
    weighted_baseline = 0.0
    weighted_helios = 0.0
    weighted_helios_baseline = 0.0
    weighted_rfp = 0.0
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weight_sum = 0.0
    trace_count = 0
    helios_trace_count = 0
    rfp_trace_count = 0

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
        rfp_val = simpoint_rob_stalls(
            rfp_dir,
            rfp_config,
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
        if baseline_val is None or rfp_val is None or ifuse_val is None or ideal_val is None:
            continue

        helios_val: float | None = None
        if include_helios and helios_dir is not None:
            helios_val = simpoint_rob_stalls(
                helios_dir,
                helios_config,
                workload,
                cluster_id,
                suite=suite,
                subsuite=subsuite,
            )

        weighted_baseline += weight * baseline_val
        weighted_rfp += weight * rfp_val
        weighted_ifuse += weight * ifuse_val
        weighted_ideal += weight * ideal_val
        if helios_val is not None:
            weighted_helios += weight * helios_val
            weighted_helios_baseline += weight * baseline_val
            helios_trace_count += 1
        weight_sum += weight
        trace_count += 1
        rfp_trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_baseline <= 0:
        return None

    helios_reduction: float | None = None
    if helios_trace_count > 0 and weighted_helios_baseline > 0:
        helios_reduction = _reduction_pct(weighted_helios_baseline, weighted_helios)

    return RobStallResult(
        workload=workload,
        baseline_stalls=weighted_baseline,
        helios_stalls=weighted_helios if helios_trace_count > 0 else None,
        rfp_stalls=weighted_rfp,
        ifuse_stalls=weighted_ifuse,
        ideal_stalls=weighted_ideal,
        helios_reduction_pct=helios_reduction,
        rfp_reduction_pct=_reduction_pct(weighted_baseline, weighted_rfp),
        ifuse_reduction_pct=_reduction_pct(weighted_baseline, weighted_ifuse),
        ideal_reduction_pct=_reduction_pct(weighted_baseline, weighted_ideal),
        trace_count=trace_count,
        helios_trace_count=helios_trace_count,
        rfp_trace_count=rfp_trace_count,
    )


def write_summary_csv(
    path: Path,
    results: list[RobStallResult],
    *,
    include_helios: bool,
) -> None:
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_baseline_stalls",
    ]
    if include_helios:
        fieldnames.extend(
            ["helios_trace_count", "weighted_helios_stalls", "helios_reduction_pct"]
        )
    fieldnames.extend(
        [
            "rfp_trace_count",
            "weighted_rfp_stalls",
            "rfp_reduction_pct",
            "weighted_ifuse_stalls",
            "ifuse_reduction_pct",
            "weighted_ideal_stalls",
            "ideal_reduction_pct",
        ]
    )

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row: dict[str, object] = {
                "workload": result.workload,
                "display_name": rename_workload(result.workload),
                "trace_count": result.trace_count,
                "weighted_baseline_stalls": f"{result.baseline_stalls:.1f}",
                "rfp_trace_count": result.rfp_trace_count,
                "weighted_rfp_stalls": f"{result.rfp_stalls:.1f}",
                "rfp_reduction_pct": f"{result.rfp_reduction_pct:.2f}",
                "weighted_ifuse_stalls": f"{result.ifuse_stalls:.1f}",
                "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                "weighted_ideal_stalls": f"{result.ideal_stalls:.1f}",
                "ideal_reduction_pct": f"{result.ideal_reduction_pct:.2f}",
            }
            if include_helios:
                row.update(
                    {
                        "helios_trace_count": result.helios_trace_count,
                        "weighted_helios_stalls": (
                            f"{result.helios_stalls:.1f}"
                            if result.helios_stalls is not None
                            else ""
                        ),
                        "helios_reduction_pct": (
                            f"{result.helios_reduction_pct:.2f}"
                            if result.helios_reduction_pct is not None
                            else ""
                        ),
                    }
                )
            writer.writerow(row)


def write_computation_log(
    path: Path,
    results: list[RobStallResult],
    *,
    include_helios: bool,
) -> None:
    with path.open("w") as fh:
        fh.write(
            "ROB stall reduction (sum of INST_LOST_ROB_STALL_* from fetch.stat.0.csv)\n"
        )
        fh.write("=" * 80 + "\n")
        for stat_name in ROB_STALL_STATS:
            fh.write(f"  + {stat_name}\n")
        fh.write("\n")
        fh.write(
            "reduction_pct = 100 * (weighted_baseline_stalls - weighted_config_stalls) "
            "/ weighted_baseline_stalls\n"
        )
        fh.write("All configurations read fetch.stat.0.csv.\n\n")
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted baseline stalls: {result.baseline_stalls:.1f}\n")
            if include_helios and result.helios_reduction_pct is not None:
                fh.write(
                    f"  weighted helios stalls:    {result.helios_stalls:.1f}  "
                    f"({result.helios_reduction_pct:.2f}% reduction, "
                    f"{result.helios_trace_count} simpoints)\n"
                )
            elif include_helios:
                fh.write("  helios: n/a\n")
            fh.write(
                f"  weighted rfp stalls:       {result.rfp_stalls:.1f}  "
                f"({result.rfp_reduction_pct:.2f}% reduction, "
                f"{result.rfp_trace_count} simpoints)\n"
            )
            fh.write(
                f"  weighted ifuse stalls:     {result.ifuse_stalls:.1f}  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted ideal stalls:     {result.ideal_stalls:.1f}  "
                f"({result.ideal_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
            rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse reduction:  {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal reduction:   {ideal_avg:.2f}%\n")
            fh.write(f"Arithmetic mean RFP reduction:     {rfp_avg:.2f}%\n")
            if include_helios:
                helios_vals = [
                    r.helios_reduction_pct
                    for r in results
                    if r.helios_reduction_pct is not None
                ]
                if helios_vals:
                    fh.write(
                        f"Arithmetic mean Helios reduction:  "
                        f"{sum(helios_vals) / len(helios_vals):.2f}%\n"
                    )


def _legend_handles(*, include_helios: bool) -> list:
    from matplotlib.patches import Patch

    handles = []
    for key, label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        handles.append(
            Patch(
                facecolor=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                label=label,
            )
        )
    return handles


def plot_rob_stall_bars(
    results: list[RobStallResult],
    output_dir: Path,
    *,
    include_helios: bool,
) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    active_series = []
    for key, _label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        attr = {
            "helios": "helios_reduction_pct",
            "rfp": "rfp_reduction_pct",
            "ifuse": "ifuse_reduction_pct",
            "ideal": "ideal_reduction_pct",
        }[key]
        values: list[float] = []
        for result in results:
            val = getattr(result, attr)
            values.append(float("nan") if val is None else val)
        finite = [v for v in values if not math.isnan(v)]
        avg = sum(finite) / len(finite) if finite else float("nan")
        values.append(avg)
        active_series.append((key, values, color))

    workloads = [result.workload for result in results]
    ordered, x_map, avg_x, separator_x = grouped_x_positions(
        workloads, n_series=len(active_series)
    )
    display_apps = [rename_workload(workload) for workload in ordered] + ["Average"]
    x = [x_map[workload] for workload in ordered] + [avg_x]
    order_indices = [workloads.index(workload) for workload in ordered]
    active_series = [
        (key, [values[index] for index in order_indices] + [values[-1]], color)
        for key, values, color in active_series
    ]
    offsets = _bar_offsets(len(active_series))

    _apply_ipc_plot_style()
    fig_w, fig_h = IPC_FIGSIZE
    fig, ax = plt.subplots(figsize=(fig_w, fig_h + 1.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (key, values, color) in zip(offsets, active_series):
        edge_colors = [
            "red" if (not math.isnan(val) and val < 0) else "black" for val in values
        ]
        edge_widths = [
            2.5 if (not math.isnan(val) and val < 0) else BAR_EDGE_WIDTH for val in values
        ]
        ax.bar(
            [i + offset for i in x],
            [0.0 if math.isnan(val) else max(0.0, val) for val in values],
            BAR_WIDTH,
            color=color,
            edgecolor=edge_colors,
            linewidth=edge_widths,
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
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for i, label in enumerate(ax.get_xticklabels()):
        label.set_fontfamily(FONT_FAMILY)
        if i == len(display_apps) - 1:
            label.set_fontweight("bold")
    _tight_x_limits(ax, x[0], x[-1], n_bars=len(active_series))

    ax.set_ylabel(
        "Reduction in\nROB stalls (%)\n(normalized to\nno-fusion)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )

    all_values = [v for _k, values, _c in active_series for v in values if not math.isnan(v)]
    ymax = max(all_values) if all_values else 100.0
    ylim_top = math.ceil((ymax * 1.15) / 20.0) * 20.0
    if ylim_top <= ymax:
        ylim_top += 20.0
    ax.set_ylim(0.0, ylim_top)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)
    _draw_app_x_tick_guides(ax, x)

    # Place the legend fully above the plot frame.
    legend = ax.legend(
        handles=_legend_handles(include_helios=include_helios),
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
    for stem in ("rob_stalls",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted ROB stall reduction for Helios, RFP, "
            "I-Fuse, and ideal fusion vs baseline."
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
        help="No-fusion baseline directory (default: baseline)",
    )
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument(
        "--include-helios",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Plot Helios bars (default: on)",
    )
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default="baseline")
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument("--workloads-db", type=Path, default=DEFAULT_WORKLOADS_DB)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/rob_stalls)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    baseline_dir = args.baseline_dir or DEFAULT_BASELINE_DIR
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR
    workloads = order_workloads_by_group(
        [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    )
    output_dir = args.output_dir or DEFAULT_ROB_STALLS_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(
        args.trace_root,
        workloads,
        workloads_db=args.workloads_db,
    )

    plot_helios = False
    if args.include_helios:
        plot_helios = helios_rob_stats_available(
            helios_dir,
            args.helios_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_helios:
            print(f"Helios skipped: missing ROB stall stats in {helios_dir}")

    print("Computing ROB stall reductions (INST_LOST_ROB_STALL_* from fetch.stat.0.csv)...")
    print(f"  baseline:     {baseline_dir} (config={args.baseline_config})")
    if plot_helios:
        print(f"  helios:       {helios_dir} (config={args.helios_config})")
    print(f"  rfp:          {rfp_dir} (config={args.rfp_config})")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config),
        "rfp": (rfp_dir, args.rfp_config),
    }
    complete_apps, reference_by_workload = check_simpoint_coverage(
        directories,
        workloads,
        sp_weights,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
        report_path=output_dir / "rob_stalls_simpoint_coverage_report.txt",
        optional_configs={"helios"},
    )
    if not complete_apps:
        raise SystemExit(
            "No apps have complete simpoint files across baseline, ifuse, ideal fusion, and rfp."
        )

    results: list[RobStallResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_stalls(
            workload,
            baseline_dir,
            helios_dir if plot_helios else None,
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
            include_helios=plot_helios,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing ROB stall stats in fetch.stat.0.csv")
            continue
        results.append(result)
        parts = [
            f"ifuse={result.ifuse_reduction_pct:5.2f}%",
            f"ideal={result.ideal_reduction_pct:5.2f}%",
        ]
        if result.helios_reduction_pct is not None:
            parts.insert(0, f"helios={result.helios_reduction_pct:5.2f}%")
        parts.insert(
            1 if result.helios_reduction_pct is not None else 0,
            f"rfp={result.rfp_reduction_pct:5.2f}%",
        )
        print(
            f"  {workload:14s}  {'  '.join(parts)}  (simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete ROB stall data.")

    write_summary_csv(
        output_dir / "rob_stalls_summary.csv",
        results,
        include_helios=plot_helios,
    )
    write_computation_log(
        output_dir / "rob_stalls_computation_log.txt",
        results,
        include_helios=plot_helios,
    )
    plot_rob_stall_bars(results, output_dir, include_helios=plot_helios)

    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
    rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    if plot_helios:
        helios_vals = [r.helios_reduction_pct for r in results if r.helios_reduction_pct is not None]
        if helios_vals:
            print(f"  Helios mean reduction:       {sum(helios_vals) / len(helios_vals):.2f}%")
    print(f"  RFP mean reduction:          {rfp_avg:.2f}%")
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
