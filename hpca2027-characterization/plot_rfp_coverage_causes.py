#!/usr/bin/env python3
"""Stacked breakdown of how RFP handles on-path load pairs.

Each bar stacks outcomes as a fraction of RFP_ALL_LOADS_count
(simpoint-weighted):
  - Covered: prefetch helped the load (full or partial mitigation)
  - Not covered: no prefetch was sent
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
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-characterization-results"
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "rfp_coverage_causes"

RFP_STAT = "rfp.stat.0.csv"

BAR_WIDTH = 0.40
FIGSIZE = (28.0, 10.0)
END_PAD = 0.45
OUTPUT_DPI = 300
Y_AXIS_LABEL = (
    "Breakdown of how RFP\n"
    "handles on-path\n"
    "load pairs (%)"
)

RFP_CLR_COVERED = "#7E57C2"
RFP_CLR_NO_PREFETCH = "#B0BEC5"
RFP_CLR_PREFETCH_NOT_USEFUL = "#FB8C00"
RFP_CLR_WRONG_ADDRESS = "#E53935"

# (field, color) — bottom-to-top stack order.
BREAKDOWN_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("covered_frac", RFP_CLR_COVERED),
    ("no_prefetch_frac", RFP_CLR_NO_PREFETCH),
    ("prefetch_not_useful_frac", RFP_CLR_PREFETCH_NOT_USEFUL),
    ("wrong_address_frac", RFP_CLR_WRONG_ADDRESS),
)

BREAKDOWN_CATEGORIES: dict[str, str] = {
    "covered_frac": "Covered",
    "no_prefetch_frac": "Not covered: no prefetch sent",
    "prefetch_not_useful_frac": "Not covered: prefetch not useful (load too early)",
    "wrong_address_frac": "Not covered: prefetch not useful (wrong address)",
}


def legend_label(field: str) -> str:
    return BREAKDOWN_CATEGORIES.get(field, field)


@dataclass
class RfpBreakdownMetrics:
    covered_frac: float
    no_prefetch_frac: float
    prefetch_not_useful_frac: float
    wrong_address_frac: float

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

    no_prefetch = max(0.0, (all_loads - predicted) + not_issued)
    wrong_frac = max(0.0, wrong) / all_loads
    prefetch_not_useful = max(0.0, load_beat) / all_loads

    return RfpBreakdownMetrics(
        covered_frac=covered / all_loads,
        no_prefetch_frac=no_prefetch / all_loads,
        prefetch_not_useful_frac=prefetch_not_useful,
        wrong_address_frac=wrong_frac,
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
            no_prefetch_frac=mean(lambda m: m.no_prefetch_frac),
            prefetch_not_useful_frac=mean(lambda m: m.prefetch_not_useful_frac),
            wrong_address_frac=mean(lambda m: m.wrong_address_frac),
        ),
    )


def write_summary_csv(path: Path, results: list[WorkloadBreakdown]) -> None:
    fields = [
        "workload",
        "display_name",
        "trace_count",
        "covered_pct",
        "not_covered_pct",
        "no_prefetch_pct",
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
                    "no_prefetch_pct": f"{b.no_prefetch_frac * 100.0:.2f}",
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
        fh.write("RFP on-path load pair breakdown\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "Each segment is simpoint-weighted as a fraction of RFP_ALL_LOADS.\n"
        )
        fh.write(
            "Covered = useful prefetch (full or partial mitigation). "
            "Among issued prefetches, useful vs not-useful fractions are also reported.\n\n"
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
            fh.write(f"  no prefetch sent:        {b.no_prefetch_frac * 100.0:6.2f}%\n")
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


def _style_legend(fig, active_segments: list[tuple[str, str]]) -> None:
    ncol = 2 if len(active_segments) <= 4 else 3
    legend = fig.legend(
        handles=_legend_handles(active_segments),
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.88),
        borderaxespad=0.0,
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        ncol=ncol,
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
        framealpha=1.0,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)


def plot_breakdown(results: list[WorkloadBreakdown], output_dir: Path) -> None:
    avg = average_breakdown(results)
    rows = results + [avg]
    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = np.arange(len(display_apps))
    active_segments = _visible_segments(rows)

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

    bottoms = np.zeros(len(rows))
    for field, color in active_segments:
        values = np.array([getattr(r.breakdown, field) * 100.0 for r in rows])
        hatch = "///" if field == "prefetch_not_useful_frac" else None
        ax.bar(
            x,
            values,
            BAR_WIDTH,
            bottom=bottoms,
            color=color,
            edgecolor="black",
            linewidth=0.8,
            hatch=hatch,
            label=legend_label(field),
            zorder=3,
        )
        bottoms += values

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
        fontsize=IPC_TICK_FONT,
        fontfamily=FONT_FAMILY,
    )
    for label in ax.get_xticklabels():
        if label.get_text() == "Average":
            label.set_weight("bold")

    half_span = BAR_WIDTH / 2.0
    ax.set_xlim(x[0] - half_span - 0.12 - END_PAD, x[-1] + half_span + 0.10 + END_PAD)
    ax.margins(x=0)

    ax.set_ylabel(
        Y_AXIS_LABEL,
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.set_ylim(0.0, 105.0)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    plt.subplots_adjust(top=0.84, bottom=0.28, left=0.08, right=0.99)
    _style_legend(fig, active_segments)

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
            pad_inches=0.05,
        )
        fig.savefig(
            output_dir / f"{stem}.pdf",
            dpi=OUTPUT_DPI,
            bbox_inches="tight",
            pad_inches=0.05,
        )
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot how RFP handles on-path load pairs by workload."
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
            f"no_prefetch={b.no_prefetch_frac * 100:5.1f}%  "
            f"pref_not_useful={b.prefetch_not_useful_total_frac * 100:5.1f}%  "
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
    print(f"  mean no prefetch:          {avg.breakdown.no_prefetch_frac * 100:.2f}%")
    print(f"  mean prefetch not useful:  {avg.breakdown.prefetch_not_useful_total_frac * 100:.2f}%")
    print(f"  mean useful of issued:     {useful_of_issued:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'rfp_coverage_causes.png'}")
    print(f"  - {output_dir / 'rfp_coverage_causes.pdf'}")
    print(f"  - {output_dir / 'rfp_coverage_causes_summary.csv'}")
    print(f"  - {output_dir / 'rfp_coverage_causes_computation_log.txt'}")


if __name__ == "__main__":
    main()
