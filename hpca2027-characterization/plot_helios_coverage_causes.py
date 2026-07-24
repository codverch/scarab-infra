#!/usr/bin/env python3
"""Stacked breakdown of how Helios handles ideally-fusible load pairs.

Each bar stacks outcomes as a fraction of IDEAL_FUSION_FUSED_LOADS_count
(simpoint-weighted). Helios fused is HELIOS_FUSIONS_COMMITTED; unfused
structural causes partition the remaining ideal pairs in proportion to
HELIOS_REJECT_* counters (excluding type-disabled rejections).

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-characterization/plot_helios_coverage_causes.py \
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

from plot_fusion_fraction import (  # noqa: E402
    HELIOS_FUSED_STAT,
    periodic_instructions,
    scale_to_measurement_window,
    stat_count_from_csv,
)
from plot_ipc import (  # noqa: E402
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_SCARAB_ROOT,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-characterization-results"
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "helios_coverage_causes"

CORE_STAT = "core.stat.0.csv"
IDEAL_STAT_FILE = "ideal_fusion.stat.0.csv"
COMMITTED_STAT = HELIOS_FUSED_STAT
IDEAL_FUSED_STAT = "IDEAL_FUSION_FUSED_LOADS_count"

BAR_WIDTH = 0.40
FIGSIZE = (24.0, 7.5)
BAR_EDGE_WIDTH = 3.0
AVERAGE_SEPARATOR_COLOR = "#2A2A2A"
AVERAGE_SEPARATOR_WIDTH = 3.5
AXIS_FONT = IPC_TICK_FONT
Y_LABEL_PAD = 20
OUTPUT_DPI = 300
Y_AXIS_LABEL = (
    "Breakdown of how Helios\n"
    "handles ideally-fusible\n"
    "load pairs (%)"
)

# (field, color) — bottom-to-top stack order.
BREAKDOWN_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("committed_frac", "#279989"),
    ("head_evicted_frac", "#0098DB"),
    ("deadlock_frac", "#D5D5D4"),
    ("addr_mismatch_frac", "#FFD700"),
    ("distance_invalid_frac", "#984EA3"),
    ("serializing_frac", "#1B9E77"),
    ("store_hazard_frac", "#A65628"),
)

BREAKDOWN_CATEGORIES: dict[str, str] = {
    "head_evicted_frac": "distance misprediction",
    "deadlock_frac": "deadlock avoidance",
    "addr_mismatch_frac": "address mismatch",
    "distance_invalid_frac": "invalid fusion distance",
    "serializing_frac": "serializing instruction",
    "store_hazard_frac": "store hazard",
}

REJECT_STATS: tuple[tuple[str, str], ...] = (
    ("head_evicted_frac", "HELIOS_REJECT_HEAD_EVICTED_count"),
    ("deadlock_frac", "HELIOS_REJECT_DEADLOCK_count"),
    ("addr_mismatch_frac", "HELIOS_REJECT_ADDR_MISMATCH_count"),
    ("distance_invalid_frac", "HELIOS_REJECT_DISTANCE_INVALID_count"),
    ("serializing_frac", "HELIOS_REJECT_SERIALIZING_count"),
    ("store_hazard_frac", "HELIOS_REJECT_STORE_HAZARD_count"),
)


def _apply_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "axes.labelsize": AXIS_FONT,
            "xtick.labelsize": AXIS_FONT,
            "ytick.labelsize": AXIS_FONT,
            "legend.fontsize": IPC_LEGEND_FONT,
        }
    )


def _tight_x_limits(ax, x_min: float, x_max: float) -> None:
    left_pad = 0.12
    right_pad = 0.12
    half_span = BAR_WIDTH / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


def legend_label(field: str) -> str:
    if field == "committed_frac":
        return "Helios fused"
    category = BREAKDOWN_CATEGORIES.get(field)
    if category is None:
        return field
    return f"Unfused: {category}"


@dataclass
class HeliosBreakdownMetrics:
    committed_frac: float
    head_evicted_frac: float
    deadlock_frac: float
    addr_mismatch_frac: float
    distance_invalid_frac: float
    serializing_frac: float
    store_hazard_frac: float


@dataclass
class WorkloadBreakdown:
    workload: str
    breakdown: HeliosBreakdownMetrics
    trace_count: int


def stat_value(stat_csv: Path, stat_name: str) -> float | None:
    return stat_count_from_csv(stat_csv, stat_name)


def scaled_helios_count(
    helios_sim: Path,
    ideal_sim: Path | None,
    stat_name: str,
) -> float | None:
    value = stat_value(helios_sim / CORE_STAT, stat_name)
    if value is None:
        return None
    return scale_to_measurement_window(
        value,
        periodic_instructions(helios_sim),
        periodic_instructions(ideal_sim),
    )


def breakdown_from_counts(
    committed: float,
    ideal_fused: float,
    reject_counts: dict[str, float],
) -> HeliosBreakdownMetrics | None:
    if ideal_fused <= 0:
        return None

    committed_frac = max(0.0, min(1.0, committed / ideal_fused))
    missed_frac = max(0.0, 1.0 - committed_frac)
    total_structural = sum(max(0.0, count) for count in reject_counts.values())

    reject_fracs: dict[str, float] = {}
    if missed_frac > 0 and total_structural > 0:
        for field, _ in REJECT_STATS:
            count = max(0.0, reject_counts.get(field, 0.0))
            reject_fracs[field] = (count / total_structural) * missed_frac
    else:
        reject_fracs = {field: 0.0 for field, _ in REJECT_STATS}

    return HeliosBreakdownMetrics(
        committed_frac=committed_frac,
        head_evicted_frac=reject_fracs["head_evicted_frac"],
        deadlock_frac=reject_fracs["deadlock_frac"],
        addr_mismatch_frac=reject_fracs["addr_mismatch_frac"],
        distance_invalid_frac=reject_fracs["distance_invalid_frac"],
        serializing_frac=reject_fracs["serializing_frac"],
        store_hazard_frac=reject_fracs["store_hazard_frac"],
    )


def helios_simpoint_breakdown(
    helios_dir: Path,
    helios_config: str,
    ideal_dir: Path,
    ideal_config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> HeliosBreakdownMetrics | None:
    helios_sim = find_simpoint_dir(
        helios_dir, helios_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    ideal_sim = find_simpoint_dir(
        ideal_dir, ideal_config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if helios_sim is None or ideal_sim is None:
        return None

    ideal_fused = stat_value(ideal_sim / IDEAL_STAT_FILE, IDEAL_FUSED_STAT)
    committed = scaled_helios_count(helios_sim, ideal_sim, COMMITTED_STAT)
    if ideal_fused is None or committed is None or ideal_fused <= 0:
        return None

    reject_counts: dict[str, float] = {}
    for field, stat_name in REJECT_STATS:
        reject_counts[field] = scaled_helios_count(helios_sim, ideal_sim, stat_name) or 0.0

    return breakdown_from_counts(committed, ideal_fused, reject_counts)


def helios_workload_breakdown(
    workload: str,
    sp_weights: dict[tuple[str, str], float],
    *,
    helios_dir: Path,
    helios_config: str,
    ideal_dir: Path,
    ideal_config: str,
    suite: str,
    subsuite: str,
) -> WorkloadBreakdown | None:
    weighted_committed = 0.0
    weighted_ideal = 0.0
    weighted_rejects: dict[str, float] = {field: 0.0 for field, _ in REJECT_STATS}
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        helios_sim = find_simpoint_dir(
            helios_dir,
            helios_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_sim = find_simpoint_dir(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        if helios_sim is None or ideal_sim is None:
            continue

        ideal_fused = stat_value(ideal_sim / IDEAL_STAT_FILE, IDEAL_FUSED_STAT)
        committed = scaled_helios_count(helios_sim, ideal_sim, COMMITTED_STAT)
        if ideal_fused is None or committed is None or ideal_fused <= 0:
            continue

        weighted_committed += weight * committed
        weighted_ideal += weight * ideal_fused
        for field, stat_name in REJECT_STATS:
            count = scaled_helios_count(helios_sim, ideal_sim, stat_name) or 0.0
            weighted_rejects[field] += weight * count
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_ideal <= 0:
        return None

    breakdown = breakdown_from_counts(
        weighted_committed,
        weighted_ideal,
        weighted_rejects,
    )
    if breakdown is None:
        return None

    return WorkloadBreakdown(
        workload=workload,
        trace_count=trace_count,
        breakdown=breakdown,
    )


def average_breakdown(results: list[WorkloadBreakdown]) -> WorkloadBreakdown:
    def mean(getter) -> float:
        return sum(getter(r.breakdown) for r in results) / len(results)

    return WorkloadBreakdown(
        workload="Average",
        trace_count=sum(r.trace_count for r in results),
        breakdown=HeliosBreakdownMetrics(
            committed_frac=mean(lambda m: m.committed_frac),
            head_evicted_frac=mean(lambda m: m.head_evicted_frac),
            deadlock_frac=mean(lambda m: m.deadlock_frac),
            addr_mismatch_frac=mean(lambda m: m.addr_mismatch_frac),
            distance_invalid_frac=mean(lambda m: m.distance_invalid_frac),
            serializing_frac=mean(lambda m: m.serializing_frac),
            store_hazard_frac=mean(lambda m: m.store_hazard_frac),
        ),
    )


def write_summary_csv(path: Path, results: list[WorkloadBreakdown]) -> None:
    fields = ["workload", "display_name", "trace_count", "helios_fused_pct"]
    fields.extend(field for field, _ in BREAKDOWN_SEGMENTS)

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            b = row.breakdown
            writer.writerow(
                {
                    "workload": row.workload,
                    "display_name": (
                        "Average"
                        if row.workload == "Average"
                        else rename_workload(row.workload)
                    ),
                    "trace_count": row.trace_count,
                    "helios_fused_pct": f"{b.committed_frac * 100.0:.2f}",
                    **{
                        field: f"{getattr(b, field) * 100.0:.2f}"
                        for field, _ in BREAKDOWN_SEGMENTS
                    },
                }
            )


def write_computation_log(path: Path, results: list[WorkloadBreakdown]) -> None:
    with path.open("w") as fh:
        fh.write("Helios ideally-fusible load pair breakdown\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "Helios fused = 100 * weighted(HELIOS_FUSIONS_COMMITTED) "
            "/ weighted(IDEAL_FUSION_FUSED_LOADS)\n"
        )
        fh.write(
            "Each unfused segment partitions the remaining ideal pairs in proportion "
            "to structural HELIOS_REJECT_* counters (type-disabled excluded).\n"
        )
        fh.write(
            "Helios counts scaled to ideal-fusion Periodic_Instructions when needed.\n\n"
        )
        for row in results:
            b = row.breakdown
            name = (
                "Average"
                if row.workload == "Average"
                else rename_workload(row.workload)
            )
            fh.write(f"{row.workload} ({name})\n")
            fh.write(f"  simpoints: {row.trace_count}\n")
            fh.write(f"  helios fused:    {b.committed_frac * 100.0:6.2f}%\n")
            fh.write(f"  distance mispred: {b.head_evicted_frac * 100.0:6.2f}%\n")
            fh.write(f"  deadlock:        {b.deadlock_frac * 100.0:6.2f}%\n")
            fh.write(f"  addr mismatch:   {b.addr_mismatch_frac * 100.0:6.2f}%\n\n")


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
        bbox_to_anchor=(0.5, 1.06),
        bbox_transform=ax.transAxes,
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

    _apply_plot_style()
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    bottoms = np.zeros(len(rows))
    for field, color in active_segments:
        values = np.array([getattr(r.breakdown, field) * 100.0 for r in rows])
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
    )
    for label in ax.get_xticklabels():
        label.set_fontsize(AXIS_FONT)
        label.set_fontfamily(FONT_FAMILY)
        if label.get_text() == "Average":
            label.set_weight("bold")

    _tight_x_limits(ax, x[0], x[-1])

    ax.set_ylabel(
        Y_AXIS_LABEL,
        fontsize=AXIS_FONT,
        fontfamily=FONT_FAMILY,
        labelpad=Y_LABEL_PAD,
    )
    ax.set_ylim(0.0, 105.0)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="both", labelsize=AXIS_FONT)
    for label in ax.get_yticklabels():
        label.set_fontsize(AXIS_FONT)
        label.set_fontfamily(FONT_FAMILY)

    plt.subplots_adjust(top=0.72, bottom=0.32, left=0.10, right=0.99)
    _style_legend(ax, active_segments)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("helios_coverage_causes",):
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
        description="Plot how Helios handles ideally-fusible load pairs by workload."
    )
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    helios_dir = args.helios_dir or (args.simulations_root / "helios")
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing Helios ideally-fusible pair breakdown...")
    print(f"  helios:       {helios_dir} (config={args.helios_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    results: list[WorkloadBreakdown] = []
    for workload in workloads:
        row = helios_workload_breakdown(
            workload,
            sp_weights,
            helios_dir=helios_dir,
            helios_config=args.helios_config,
            ideal_dir=ideal_dir,
            ideal_config=args.ideal_fusion_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if row is None:
            print(f"  skip {workload}: missing Helios/ideal-fusion stats")
            continue
        b = row.breakdown
        print(
            f"  {workload:14s}  fused={b.committed_frac * 100:5.1f}%  "
            f"head_evict={b.head_evicted_frac * 100:5.1f}%  "
            f"deadlock={b.deadlock_frac * 100:5.1f}%  "
            f"(simpoints={row.trace_count})"
        )
        results.append(row)

    if not results:
        raise SystemExit("No workloads with complete Helios fusion breakdown data.")

    write_summary_csv(output_dir / "helios_coverage_causes_summary.csv", results)
    write_computation_log(output_dir / "helios_coverage_causes_computation_log.txt", results)
    plot_breakdown(results, output_dir)

    avg = average_breakdown(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  mean helios fused: {avg.breakdown.committed_frac * 100:.2f}%")
    print(f"  mean head evict:   {avg.breakdown.head_evicted_frac * 100:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'helios_coverage_causes.png'}")
    print(f"  - {output_dir / 'helios_coverage_causes.pdf'}")
    print(f"  - {output_dir / 'helios_coverage_causes_summary.csv'}")
    print(f"  - {output_dir / 'helios_coverage_causes_computation_log.txt'}")


if __name__ == "__main__":
    main()
