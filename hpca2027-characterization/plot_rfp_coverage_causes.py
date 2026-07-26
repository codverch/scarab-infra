#!/usr/bin/env python3
"""Stacked breakdown of how RFP handles on-path loads.

Each bar stacks outcomes as a fraction of RFP_ALL_LOADS_count
(simpoint-weighted):
  - Covered: prefetch helped the load (full or partial mitigation)
  - Not covered: low predictor confidence
  - Not covered: prefetch sent but not useful (load too early or wrong address)

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-characterization/plot_rfp_coverage_causes.py \
  --simulations-root /users/deepmish/scarab/src/simulations
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
MAIN_GRAPHS = SCARAB_INFRA_ROOT / "hpca2027-main-graphs"
if str(MAIN_GRAPHS) not in sys.path:
    sys.path.insert(0, str(MAIN_GRAPHS))

from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    BAR_EDGE_WIDTH,
    DEFAULT_RFP_CONFIG,
    DEFAULT_RFP_DIR,
    DEFAULT_SCARAB_ROOT,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    IPC_TICK_FONT,
    RFP_COLOR,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-characterization-results"
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "rfp_coverage_causes"

RFP_STAT = "rfp.stat.0.csv"

BAR_WIDTH = 0.40
FIGSIZE = (24.0, 8.0)
AVERAGE_SEPARATOR_WIDTH = 3.5
AXIS_FONT = IPC_TICK_FONT
LEGEND_FONT = 28
Y_LABEL_PAD = 20
OUTPUT_DPI = 300
LOAD_BEAT_PLOT_THRESHOLD = 0.005  # hide negligible load-beat slices (<0.5%)
Y_AXIS_LABEL = (
    "Breakdown of how RFP\n"
    "handles memory\n"
    "loads (%)"
)

LOW_CONFIDENCE_COLOR = "#A81423"  # backend-stalls red; distinct from RFP_COLOR
RFP_CLR_PREFETCH_NOT_USEFUL = "#D5D5D4"
RFP_CLR_WRONG_ADDRESS = "#FFD92F"

# (field, color) — bottom-to-top stack order.
BREAKDOWN_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("covered_frac", RFP_COLOR),
    ("prefetch_not_useful_frac", RFP_CLR_PREFETCH_NOT_USEFUL),
    ("wrong_address_frac", RFP_CLR_WRONG_ADDRESS),
    ("low_confidence_frac", LOW_CONFIDENCE_COLOR),
)

BREAKDOWN_CATEGORIES: dict[str, str] = {
    "covered_frac": "Covered",
    "low_confidence_frac": "Not covered: low predictor confidence",
    "prefetch_not_useful_frac": "Not covered: prefetch not useful (load too early)",
    "wrong_address_frac": "Not covered: prefetch not useful (wrong address)",
}


def legend_label(field: str) -> str:
    return BREAKDOWN_CATEGORIES.get(field, field)


def _apply_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "axes.labelsize": AXIS_FONT,
            "xtick.labelsize": AXIS_FONT,
            "ytick.labelsize": AXIS_FONT,
            "legend.fontsize": LEGEND_FONT,
            "text.color": "black",
            "axes.labelcolor": "black",
            "xtick.color": "black",
            "ytick.color": "black",
        }
    )


def _tight_x_limits(ax, x_min: float, x_max: float) -> None:
    left_pad = 0.12
    right_pad = 0.12
    half_span = BAR_WIDTH / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


@dataclass
class RfpBreakdownMetrics:
    covered_frac: float
    prefetch_not_useful_frac: float
    wrong_address_frac: float
    low_confidence_frac: float

    @property
    def not_covered_frac(self) -> float:
        return 1.0 - self.covered_frac

    @property
    def prefetch_useful_frac(self) -> float:
        return self.covered_frac

    @property
    def prefetch_not_useful_total_frac(self) -> float:
        return self.prefetch_not_useful_frac + self.wrong_address_frac


@dataclass
class WorkloadBreakdown:
    workload: str
    breakdown: RfpBreakdownMetrics
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


def breakdown_from_counts(
    all_loads: float,
    predicted: float,
    not_issued: float,
    issued: float,
    useful: float,
    dropped: float,
    wrong: float,
    full: float,
    partial: float,
) -> RfpBreakdownMetrics | None:
    if all_loads <= 0:
        return None

    load_beat = min(dropped, max(0.0, issued - useful - wrong))
    covered = max(0.0, full + partial)
    if useful is not None and useful > 0:
        covered = max(covered, useful)
    covered = min(covered, all_loads)

    low_confidence = max(0.0, (all_loads - predicted) + not_issued)
    wrong_frac = max(0.0, wrong) / all_loads
    prefetch_not_useful = max(0.0, load_beat) / all_loads

    return RfpBreakdownMetrics(
        covered_frac=covered / all_loads,
        prefetch_not_useful_frac=prefetch_not_useful,
        wrong_address_frac=wrong_frac,
        low_confidence_frac=low_confidence / all_loads,
    )


def rfp_simpoint_breakdown(
    rfp_dir: Path,
    rfp_config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> RfpBreakdownMetrics | None:
    rfp_sim = find_simpoint_dir(
        rfp_dir, rfp_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if rfp_sim is None:
        return None

    rfp_csv = rfp_sim / RFP_STAT
    all_loads = stat_value(rfp_csv, "RFP_ALL_LOADS_count")
    predicted = stat_value(rfp_csv, "RFP_PREDICTION_MADE_count")
    not_issued = stat_value(rfp_csv, "RFP_ELIGIBLE_NOT_ISSUED_count")
    issued = stat_value(rfp_csv, "RFP_PREFETCH_INJECTED_count")
    useful = stat_value(rfp_csv, "RFP_PREFETCH_USEFUL_count")
    dropped = stat_value(rfp_csv, "RFP_DROPPED_SINCE_LOAD_BEAT_PREFETCH_count")
    wrong = stat_value(rfp_csv, "RFP_PREDICTION_WRONG_count")
    full = stat_value(rfp_csv, "RFP_FULL_MITIGATED_count")
    partial = stat_value(rfp_csv, "RFP_PARTIAL_MITIGATED_count")
    if (
        all_loads is None
        or predicted is None
        or not_issued is None
        or issued is None
        or useful is None
        or dropped is None
        or wrong is None
        or full is None
        or partial is None
    ):
        return None

    return breakdown_from_counts(
        all_loads,
        predicted,
        not_issued,
        issued,
        useful,
        dropped,
        wrong,
        full,
        partial,
    )


def rfp_workload_breakdown(
    workload: str,
    sp_weights: dict[tuple[str, str], float],
    *,
    rfp_dir: Path,
    rfp_config: str,
    suite: str,
    subsuite: str,
) -> WorkloadBreakdown | None:
    weighted_fracs: dict[str, float] = {field: 0.0 for field, _ in BREAKDOWN_SEGMENTS}
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        breakdown = rfp_simpoint_breakdown(
            rfp_dir,
            rfp_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        if breakdown is None:
            continue
        for field, _ in BREAKDOWN_SEGMENTS:
            weighted_fracs[field] += weight * getattr(breakdown, field)
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0:
        return None

    return WorkloadBreakdown(
        workload=workload,
        trace_count=trace_count,
        breakdown=RfpBreakdownMetrics(
            **{field: weighted_fracs[field] / weight_sum for field, _ in BREAKDOWN_SEGMENTS}
        ),
    )


def average_breakdown(results: list[WorkloadBreakdown]) -> WorkloadBreakdown:
    def mean(getter) -> float:
        return sum(getter(r.breakdown) for r in results) / len(results)

    return WorkloadBreakdown(
        workload="Average",
        trace_count=sum(r.trace_count for r in results),
        breakdown=RfpBreakdownMetrics(
            covered_frac=mean(lambda m: m.covered_frac),
            prefetch_not_useful_frac=mean(lambda m: m.prefetch_not_useful_frac),
            wrong_address_frac=mean(lambda m: m.wrong_address_frac),
            low_confidence_frac=mean(lambda m: m.low_confidence_frac),
        ),
    )


def write_summary_csv(path: Path, results: list[WorkloadBreakdown]) -> None:
    fields = [
        "workload",
        "display_name",
        "trace_count",
        "covered_pct",
        "not_covered_pct",
        "low_confidence_pct",
        "prefetch_not_useful_pct",
        "wrong_address_pct",
        "prefetch_useful_of_issued_pct",
        "prefetch_not_useful_of_issued_pct",
    ]
    fields.extend(field for field, _ in BREAKDOWN_SEGMENTS)

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            b = row.breakdown
            issued_frac = b.covered_frac + b.prefetch_not_useful_total_frac
            useful_of_issued = (
                100.0 * b.covered_frac / issued_frac if issued_frac > 0 else float("nan")
            )
            not_useful_of_issued = (
                100.0 * b.prefetch_not_useful_total_frac / issued_frac
                if issued_frac > 0
                else float("nan")
            )
            writer.writerow(
                {
                    "workload": row.workload,
                    "display_name": (
                        "Average"
                        if row.workload == "Average"
                        else rename_workload(row.workload)
                    ),
                    "trace_count": row.trace_count,
                    "covered_pct": f"{b.covered_frac * 100.0:.2f}",
                    "not_covered_pct": f"{b.not_covered_frac * 100.0:.2f}",
                    "low_confidence_pct": f"{b.low_confidence_frac * 100.0:.2f}",
                    "prefetch_not_useful_pct": f"{b.prefetch_not_useful_frac * 100.0:.2f}",
                    "wrong_address_pct": f"{b.wrong_address_frac * 100.0:.2f}",
                    "prefetch_useful_of_issued_pct": f"{useful_of_issued:.2f}",
                    "prefetch_not_useful_of_issued_pct": f"{not_useful_of_issued:.2f}",
                    **{
                        field: f"{getattr(b, field) * 100.0:.2f}"
                        for field, _ in BREAKDOWN_SEGMENTS
                    },
                }
            )


def write_computation_log(path: Path, results: list[WorkloadBreakdown]) -> None:
    with path.open("w") as fh:
        fh.write("RFP on-path load breakdown\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "Each segment is simpoint-weighted as a fraction of RFP_ALL_LOADS.\n"
        )
        fh.write(
            "Low-confidence bucket includes loads with no PT prediction and loads "
            "where a prefetch was eligible but not injected (e.g., queue/resource limits).\n\n"
        )
        for row in results:
            b = row.breakdown
            name = (
                "Average"
                if row.workload == "Average"
                else rename_workload(row.workload)
            )
            issued_frac = b.covered_frac + b.prefetch_not_useful_total_frac
            useful_of_issued = (
                100.0 * b.covered_frac / issued_frac if issued_frac > 0 else float("nan")
            )
            not_useful_of_issued = (
                100.0 * b.prefetch_not_useful_total_frac / issued_frac
                if issued_frac > 0
                else float("nan")
            )
            fh.write(f"{row.workload} ({name})\n")
            fh.write(f"  simpoints: {row.trace_count}\n")
            fh.write(f"  covered:                 {b.covered_frac * 100.0:6.2f}%\n")
            fh.write(f"  low confidence / not sent: {b.low_confidence_frac * 100.0:6.2f}%\n")
            fh.write(
                f"  prefetch not useful:     {b.prefetch_not_useful_total_frac * 100.0:6.2f}%\n"
            )
            fh.write(
                f"    load too early:        {b.prefetch_not_useful_frac * 100.0:6.2f}%\n"
            )
            fh.write(f"    wrong address:         {b.wrong_address_frac * 100.0:6.2f}%\n")
            fh.write(
                f"  issued prefetches useful:     {useful_of_issued:6.2f}%\n"
            )
            fh.write(
                f"  issued prefetches not useful: {not_useful_of_issued:6.2f}%\n\n"
            )


def breakdown_for_plot(breakdown: RfpBreakdownMetrics) -> RfpBreakdownMetrics:
    """Fold negligible load-beat slices into low-confidence for readability."""
    load_beat = breakdown.prefetch_not_useful_frac
    if load_beat >= LOAD_BEAT_PLOT_THRESHOLD:
        return breakdown
    return RfpBreakdownMetrics(
        covered_frac=breakdown.covered_frac,
        prefetch_not_useful_frac=0.0,
        wrong_address_frac=breakdown.wrong_address_frac,
        low_confidence_frac=breakdown.low_confidence_frac + load_beat,
    )


def _visible_segments(rows: list[WorkloadBreakdown]) -> list[tuple[str, str]]:
    visible: list[tuple[str, str]] = []
    for field, color in BREAKDOWN_SEGMENTS:
        values = [getattr(r.breakdown, field) * 100.0 for r in rows]
        if max(values) > 0.01:
            visible.append((field, color))
    return visible


def _legend_handles(active_segments: list[tuple[str, str]]) -> list:
    from matplotlib.patches import Patch

    return [
        Patch(
            facecolor=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            label=legend_label(field),
        )
        for field, color in active_segments
    ]


def _style_legend(ax, active_segments: list[tuple[str, str]]) -> None:
    ncol = 2 if len(active_segments) > 1 else 1
    legend = ax.legend(
        handles=_legend_handles(active_segments),
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.12),
        bbox_transform=ax.transAxes,
        borderaxespad=0.0,
        fontsize=LEGEND_FONT,
        edgecolor="black",
        ncol=ncol,
        handlelength=1.2,
        handleheight=0.9,
        columnspacing=1.0,
        framealpha=1.0,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)
    for text in legend.get_texts():
        text.set_color("black")
        text.set_fontfamily(FONT_FAMILY)
        text.set_fontsize(LEGEND_FONT)


def plot_breakdown(results: list[WorkloadBreakdown], output_dir: Path) -> None:
    avg = average_breakdown(results)
    rows = results + [avg]
    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = np.arange(len(display_apps))

    plot_rows = [
        WorkloadBreakdown(
            workload=row.workload,
            trace_count=row.trace_count,
            breakdown=breakdown_for_plot(row.breakdown),
        )
        for row in rows
    ]
    active_segments = _visible_segments(plot_rows)

    _apply_plot_style()
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    bottoms = np.zeros(len(plot_rows))
    for field, color in active_segments:
        values = np.array([getattr(r.breakdown, field) * 100.0 for r in plot_rows])
        ax.bar(
            x,
            values,
            BAR_WIDTH,
            bottom=bottoms,
            color=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            label=legend_label(field),
            zorder=3,
        )
        bottoms += values

    if len(display_apps) > 1:
        ax.axvline(
            x=len(display_apps) - 1.5,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            linewidth=AVERAGE_SEPARATOR_WIDTH,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        display_apps,
        rotation=45,
        ha="right",
        fontsize=AXIS_FONT,
        fontfamily=FONT_FAMILY,
        color="black",
    )
    for label in ax.get_xticklabels():
        label.set_fontsize(AXIS_FONT)
        label.set_fontfamily(FONT_FAMILY)
        label.set_color("black")
        if label.get_text() == "Average":
            label.set_weight("bold")

    _tight_x_limits(ax, x[0], x[-1])

    ax.set_ylabel(
        Y_AXIS_LABEL,
        fontsize=AXIS_FONT,
        fontfamily=FONT_FAMILY,
        color="black",
        labelpad=Y_LABEL_PAD,
    )
    ax.set_ylim(0.0, 105.0)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="both", labelsize=AXIS_FONT, colors="black")
    for label in ax.get_yticklabels():
        label.set_fontsize(AXIS_FONT)
        label.set_fontfamily(FONT_FAMILY)
        label.set_color("black")

    plt.subplots_adjust(top=0.70, bottom=0.32, left=0.10, right=0.99)
    _style_legend(ax, active_segments)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("rfp_coverage_causes",):
        fig.savefig(
            output_dir / f"{stem}.png",
            dpi=OUTPUT_DPI,
            bbox_inches="tight",
            pad_inches=0.08,
        )
        fig.savefig(
            output_dir / f"{stem}.pdf",
            dpi=OUTPUT_DPI,
            bbox_inches="tight",
            pad_inches=0.08,
        )
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot how RFP handles on-path loads by workload."
    )
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    rfp_dir = args.rfp_dir or (args.simulations_root / "rfp")
    if args.rfp_dir is None and DEFAULT_RFP_DIR.is_dir():
        rfp_dir = DEFAULT_RFP_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing RFP on-path load breakdown...")
    print(f"  rfp:    {rfp_dir} (config={args.rfp_config})")
    print(f"  output: {output_dir}")

    results: list[WorkloadBreakdown] = []
    for workload in workloads:
        row = rfp_workload_breakdown(
            workload,
            sp_weights,
            rfp_dir=rfp_dir,
            rfp_config=args.rfp_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if row is None:
            print(f"  skip {workload}: missing RFP stats")
            continue
        b = row.breakdown
        issued_frac = b.covered_frac + b.prefetch_not_useful_total_frac
        useful_of_issued = (
            100.0 * b.covered_frac / issued_frac if issued_frac > 0 else float("nan")
        )
        print(
            f"  {workload:14s}  covered={b.covered_frac * 100:5.1f}%  "
            f"low_conf={b.low_confidence_frac * 100:5.1f}%  "
            f"load_beat={b.prefetch_not_useful_frac * 100:5.1f}%  "
            f"wrong_addr={b.wrong_address_frac * 100:5.1f}%  "
            f"useful_of_issued={useful_of_issued:5.1f}%  "
            f"(simpoints={row.trace_count})"
        )
        results.append(row)

    if not results:
        raise SystemExit("No workloads with complete RFP coverage breakdown data.")

    write_summary_csv(output_dir / "rfp_coverage_causes_summary.csv", results)
    write_computation_log(output_dir / "rfp_coverage_causes_computation_log.txt", results)
    plot_breakdown(results, output_dir)

    avg = average_breakdown(results)
    issued_frac = avg.breakdown.covered_frac + avg.breakdown.prefetch_not_useful_total_frac
    useful_of_issued = (
        100.0 * avg.breakdown.covered_frac / issued_frac if issued_frac > 0 else float("nan")
    )
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  mean covered:              {avg.breakdown.covered_frac * 100:.2f}%")
    print(f"  mean low confidence:       {avg.breakdown.low_confidence_frac * 100:.2f}%")
    print(f"  mean prefetch not useful:  {avg.breakdown.prefetch_not_useful_total_frac * 100:.2f}%")
    print(f"  mean useful of issued:     {useful_of_issued:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'rfp_coverage_causes.png'}")
    print(f"  - {output_dir / 'rfp_coverage_causes.pdf'}")
    print(f"  - {output_dir / 'rfp_coverage_causes_summary.csv'}")
    print(f"  - {output_dir / 'rfp_coverage_causes_computation_log.txt'}")


if __name__ == "__main__":
    main()
