#!/usr/bin/env python3
"""Simpoint-weighted L1-D cache access reduction for Helios, RFP, I-Fuse, and ideal fusion vs baseline.

Counts L1-D array touches as:
  baseline / I-Fuse / ideal: DCACHE_ACCESS_ONPATH_count + DCACHE_ACCESS_OFFPATH_count
  Helios: DCACHE_HIT_ONPATH/OFFPATH + DCACHE_MISS_ONPATH/OFFPATH (Helios sims lack DCACHE_ACCESS)
  RFP: DCACHE_ACCESS_* + RFP_PREFETCH_EXECUTED_count from rfp.stat.0.csv

Baseline and I-Fuse read memory.stat.0.csv; Helios reads memory.stat.0.csv; RFP also reads
rfp.stat.0.csv; ideal fusion reads ideal_fusion.stat.0.csv. All schemes use the same baseline
denominator: DCACHE_ACCESS_* from rfp-baseline/.

Per workload:
  reduction_pct = 100 * (weighted_baseline_accesses - weighted_config_accesses)
                  / weighted_baseline_accesses

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_dcache_accesses.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --rfp-dir /users/deepmish/scarab/src/simulations/rfp \
  --helios-dir /users/deepmish/scarab/src/simulations/helios \
  --dcache-baseline-dir /users/deepmish/scarab/src/simulations/rfp-baseline \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --ideal-fusion-dir /users/deepmish/scarab/src/simulations/ideal-fusion \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/dcache_accesses
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
    BAR_WIDTH,
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_BASELINE_DIR,
    DEFAULT_DCACHE_ACCESSES_OUTPUT_DIR,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_RFP_BASELINE_DIR,
    DEFAULT_RFP_CONFIG,
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
    check_simpoint_coverage,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

DCACHE_ACCESS_ONPATH_STAT = "DCACHE_ACCESS_ONPATH_count"
DCACHE_ACCESS_OFFPATH_STAT = "DCACHE_ACCESS_OFFPATH_count"
HELIOS_DCACHE_HIT_ONPATH_STAT = "DCACHE_HIT_ONPATH_count"
HELIOS_DCACHE_HIT_OFFPATH_STAT = "DCACHE_HIT_OFFPATH_count"
HELIOS_DCACHE_MISS_ONPATH_STAT = "DCACHE_MISS_ONPATH_count"
HELIOS_DCACHE_MISS_OFFPATH_STAT = "DCACHE_MISS_OFFPATH_count"
RFP_PREFETCH_EXECUTED_STAT = "RFP_PREFETCH_EXECUTED_count"
MEMORY_STAT_FILE = "memory.stat.0.csv"
RFP_STAT_FILE = "rfp.stat.0.csv"
IDEAL_STAT_FILE = "ideal_fusion.stat.0.csv"

DCACHE_SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ideal", "Ideal fusion", IDEAL_FUSION_COLOR),
)


@dataclass
class DcacheAccessResult:
    workload: str
    baseline_accesses: float
    helios_accesses: float | None
    rfp_accesses: float
    ifuse_accesses: float
    ideal_accesses: float
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


def total_dcache_accesses_from_csv(stat_csv: Path) -> float | None:
    onpath = stat_count_from_csv(stat_csv, DCACHE_ACCESS_ONPATH_STAT)
    offpath = stat_count_from_csv(stat_csv, DCACHE_ACCESS_OFFPATH_STAT)
    if onpath is None or offpath is None:
        return None
    return onpath + offpath


def total_helios_dcache_accesses_from_csv(stat_csv: Path) -> float | None:
    stats = [
        HELIOS_DCACHE_HIT_ONPATH_STAT,
        HELIOS_DCACHE_HIT_OFFPATH_STAT,
        HELIOS_DCACHE_MISS_ONPATH_STAT,
        HELIOS_DCACHE_MISS_OFFPATH_STAT,
    ]
    values = [stat_count_from_csv(stat_csv, stat_name) for stat_name in stats]
    if any(value is None for value in values):
        return None
    return sum(values)


def total_rfp_dcache_accesses_from_sim_dir(sim_dir: Path) -> float | None:
    dcache_accesses = total_dcache_accesses_from_csv(sim_dir / MEMORY_STAT_FILE)
    if dcache_accesses is None:
        return None
    prefetch_executed = stat_count_from_csv(sim_dir / RFP_STAT_FILE, RFP_PREFETCH_EXECUTED_STAT)
    if prefetch_executed is None:
        return None
    return dcache_accesses + prefetch_executed


def simpoint_dcache_accesses(
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
    return total_dcache_accesses_from_csv(sim_dir / stat_file)


def simpoint_rfp_dcache_accesses(
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
    return total_rfp_dcache_accesses_from_sim_dir(sim_dir)


def simpoint_helios_dcache_accesses(
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
    return total_helios_dcache_accesses_from_csv(sim_dir / MEMORY_STAT_FILE)


def helios_dcache_stats_available(
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
                stat_count_from_csv(sim_dir / MEMORY_STAT_FILE, HELIOS_DCACHE_HIT_ONPATH_STAT)
                is not None
            ):
                return True
    return False


def compute_workload_accesses(
    workload: str,
    dcache_baseline_dir: Path,
    helios_dir: Path | None,
    rfp_dir: Path,
    ifuse_dir: Path,
    ideal_dir: Path,
    reference_traces: set[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    dcache_baseline_config: str,
    helios_config: str,
    rfp_config: str,
    ifuse_config: str,
    ideal_config: str,
    include_helios: bool,
    suite: str,
    subsuite: str,
) -> DcacheAccessResult | None:
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

        baseline_val = simpoint_dcache_accesses(
            dcache_baseline_dir,
            dcache_baseline_config,
            workload,
            cluster_id,
            stat_file=MEMORY_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        rfp_val = simpoint_rfp_dcache_accesses(
            rfp_dir,
            rfp_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        ifuse_val = simpoint_dcache_accesses(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file=MEMORY_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_val = simpoint_dcache_accesses(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file=IDEAL_STAT_FILE,
            suite=suite,
            subsuite=subsuite,
        )
        if baseline_val is None or rfp_val is None or ifuse_val is None or ideal_val is None:
            continue

        helios_val: float | None = None
        if include_helios and helios_dir is not None:
            helios_val = simpoint_helios_dcache_accesses(
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

    helios_reduction_pct: float | None = None
    if helios_trace_count > 0 and weighted_helios_baseline > 0:
        helios_reduction_pct = (
            100.0
            * (weighted_helios_baseline - weighted_helios)
            / weighted_helios_baseline
        )

    return DcacheAccessResult(
        workload=workload,
        baseline_accesses=weighted_baseline,
        helios_accesses=weighted_helios if helios_trace_count > 0 else None,
        rfp_accesses=weighted_rfp,
        ifuse_accesses=weighted_ifuse,
        ideal_accesses=weighted_ideal,
        helios_reduction_pct=helios_reduction_pct,
        rfp_reduction_pct=100.0 * (weighted_baseline - weighted_rfp) / weighted_baseline,
        ifuse_reduction_pct=100.0 * (weighted_baseline - weighted_ifuse) / weighted_baseline,
        ideal_reduction_pct=100.0 * (weighted_baseline - weighted_ideal) / weighted_baseline,
        trace_count=trace_count,
        helios_trace_count=helios_trace_count,
        rfp_trace_count=rfp_trace_count,
    )


def write_summary_csv(
    path: Path,
    results: list[DcacheAccessResult],
    *,
    include_helios: bool,
) -> None:
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_baseline_accesses",
    ]
    if include_helios:
        fieldnames.extend(
            [
                "helios_trace_count",
                "weighted_helios_accesses",
                "helios_reduction_pct",
            ]
        )
    fieldnames.extend(
        [
            "weighted_rfp_accesses",
            "rfp_reduction_pct",
            "weighted_ifuse_accesses",
            "ifuse_reduction_pct",
            "weighted_ideal_accesses",
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
                "weighted_baseline_accesses": f"{result.baseline_accesses:.1f}",
                "weighted_rfp_accesses": f"{result.rfp_accesses:.1f}",
                "rfp_reduction_pct": f"{result.rfp_reduction_pct:.2f}",
                "weighted_ifuse_accesses": f"{result.ifuse_accesses:.1f}",
                "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                "weighted_ideal_accesses": f"{result.ideal_accesses:.1f}",
                "ideal_reduction_pct": f"{result.ideal_reduction_pct:.2f}",
            }
            if include_helios:
                row.update(
                    {
                        "helios_trace_count": result.helios_trace_count,
                        "weighted_helios_accesses": (
                            f"{result.helios_accesses:.1f}"
                            if result.helios_accesses is not None
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
    results: list[DcacheAccessResult],
    *,
    include_helios: bool,
) -> None:
    with path.open("w") as fh:
        fh.write(
            "L1-D cache access reduction\n"
            "  baseline / I-Fuse / ideal: DCACHE_ACCESS_ONPATH_count + DCACHE_ACCESS_OFFPATH_count\n"
        )
        if include_helios:
            fh.write(
                "  Helios: DCACHE_HIT_ONPATH/OFFPATH + DCACHE_MISS_ONPATH/OFFPATH "
                "(Helios sims lack DCACHE_ACCESS)\n"
            )
        fh.write(
            "  RFP: DCACHE_ACCESS_* + RFP_PREFETCH_EXECUTED_count from rfp.stat.0.csv\n"
        )
        fh.write("=" * 80 + "\n")
        fh.write(
            "reduction_pct = 100 * (weighted_baseline - weighted_config) / weighted_baseline\n"
        )
        fh.write(
            "Baseline uses DCACHE_ACCESS_* from rfp-baseline/. Helios reads memory.stat.0.csv; "
            "RFP reads memory.stat.0.csv and rfp.stat.0.csv; I-Fuse reads memory.stat.0.csv; "
            "ideal fusion reads ideal_fusion.stat.0.csv.\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted baseline accesses: {result.baseline_accesses:.1f}\n")
            if include_helios and result.helios_reduction_pct is not None:
                fh.write(
                    f"  weighted helios accesses:   {result.helios_accesses:.1f}  "
                    f"({result.helios_reduction_pct:.2f}% reduction, "
                    f"{result.helios_trace_count} simpoints)\n"
                )
            fh.write(
                f"  weighted rfp accesses:      {result.rfp_accesses:.1f}  "
                f"({result.rfp_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted ifuse accesses:    {result.ifuse_accesses:.1f}  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted ideal accesses:    {result.ideal_accesses:.1f}  "
                f"({result.ideal_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
            rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean RFP reduction:      {rfp_avg:.2f}%\n")
            if include_helios:
                helios_values = [
                    r.helios_reduction_pct
                    for r in results
                    if r.helios_reduction_pct is not None
                ]
                if helios_values:
                    helios_avg = sum(helios_values) / len(helios_values)
                    fh.write(f"Arithmetic mean Helios reduction:  {helios_avg:.2f}%\n")
            fh.write(f"Arithmetic mean I-Fuse reduction:  {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal reduction:   {ideal_avg:.2f}%\n")


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]


def plot_dcache_reduction_bars(
    results: list[DcacheAccessResult],
    output_dir: Path,
    *,
    include_helios: bool,
) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    def display_pct(pct: float | None) -> float:
        if pct is None:
            return 0.0
        return max(0.0, pct)

    active_series: list[tuple[str, list[float], str]] = []
    for key, label, color in DCACHE_SERIES:
        if key == "helios" and not include_helios:
            continue
        attr = {
            "helios": "helios_reduction_pct",
            "rfp": "rfp_reduction_pct",
            "ifuse": "ifuse_reduction_pct",
            "ideal": "ideal_reduction_pct",
        }[key]
        values = [display_pct(getattr(result, attr)) for result in results]
        values.append(sum(values) / len(values))
        active_series.append((label, values, color))

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
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
    fig, ax = plt.subplots(figsize=(24, 6.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (_label, values, color) in zip(offsets, active_series):
        ax.bar(
            [i + offset for i in x],
            values,
            BAR_WIDTH,
            label=_label,
            color=color,
            edgecolor="black",
            linewidth=1.0,
            zorder=3,
        )

    for i in range(len(display_apps)):
        for offset, (label, values, color) in zip(offsets, active_series):
            if label == "RFP":
                continue
            val = values[i]
            if 0 <= val < ARROW_THRESHOLD:
                ax.annotate(
                    "",
                    xy=(i + offset, 0),
                    xytext=(i + offset, 5.5),
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.5, mutation_scale=12),
                    zorder=10,
                )
                ax.text(
                    i + offset - 0.12,
                    5.5,
                    f"{val:.1f}",
                    ha="center",
                    va="bottom",
                    fontsize=IPC_TICK_FONT,
                    fontfamily=FONT_FAMILY,
                    color=color,
                    zorder=10,
                )

    if len(display_apps) > 1:
        ax.axvline(
            x=len(display_apps) - 1.5,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            alpha=0.8,
            linewidth=2.5,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(display_apps, rotation=45, ha="right", fontfamily=FONT_FAMILY)
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    ax.set_ylabel(
        "Reduction in number of\nL1-D cache accesses (%)\n(normalized to no-fusion)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ymax = max(value for _label, values, _color in active_series for value in values)
    ax.set_ylim(0.0, ymax * 1.12 + 2.0)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    plt.subplots_adjust(top=0.88, bottom=0.28, left=0.08, right=0.99)

    legend = ax.legend(
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
    legend.get_frame().set_linewidth(2.0)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("dcache_accesses",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted L1-D cache access reduction for Helios, RFP, I-Fuse, "
            "and ideal fusion vs baseline."
        )
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument(
        "--dcache-baseline-dir",
        type=Path,
        default=None,
        help="Baseline with DCACHE_ACCESS_* stats (default: simulations/rfp-baseline)",
    )
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument(
        "--include-helios",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Plot Helios bars (default: on)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument(
        "--dcache-baseline-config",
        default=DEFAULT_BASELINE_CONFIG,
        help="Config name under dcache-baseline-dir (default: baseline)",
    )
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/dcache_accesses)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    baseline_dir = args.baseline_dir or DEFAULT_BASELINE_DIR
    dcache_baseline_dir = args.dcache_baseline_dir or DEFAULT_RFP_BASELINE_DIR
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_DCACHE_ACCESSES_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    plot_helios = False
    if args.include_helios:
        plot_helios = helios_dcache_stats_available(
            helios_dir,
            args.helios_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_helios:
            print(
                "Helios skipped: missing DCACHE_HIT_ONPATH_count in "
                f"{helios_dir}"
            )

    print("Computing L1-D cache access reductions (on-path + off-path)...")
    print(
        f"  dcache baseline: {dcache_baseline_dir} "
        f"(config={args.dcache_baseline_config})"
    )
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
        report_path=output_dir / "dcache_accesses_simpoint_coverage_report.txt",
    )
    if not complete_apps:
        raise SystemExit(
            "No apps have complete simpoint files across baseline, ifuse, and ideal fusion."
        )

    results: list[DcacheAccessResult] = []
    for workload in workloads:
        if workload not in complete_apps:
            print(f"  skip {workload}: incomplete simpoint coverage")
            continue
        result = compute_workload_accesses(
            workload,
            dcache_baseline_dir,
            helios_dir if plot_helios else None,
            rfp_dir,
            ifuse_dir,
            ideal_dir,
            reference_by_workload[workload],
            sp_weights,
            dcache_baseline_config=args.dcache_baseline_config,
            helios_config=args.helios_config,
            rfp_config=args.rfp_config,
            ifuse_config=args.ifuse_config,
            ideal_config=args.ideal_fusion_config,
            include_helios=plot_helios,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(
                f"  skip {workload}: missing DCACHE_ACCESS_* or RFP_PREFETCH_EXECUTED stats"
            )
            continue
        results.append(result)
        helios_msg = (
            f"helios={result.helios_reduction_pct:5.2f}%  "
            if result.helios_reduction_pct is not None
            else ""
        )
        print(
            f"  {workload:14s}  {helios_msg}"
            f"rfp={result.rfp_reduction_pct:5.2f}%  "
            f"ifuse={result.ifuse_reduction_pct:5.2f}%  "
            f"ideal={result.ideal_reduction_pct:5.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete L1-D cache access data.")

    write_summary_csv(
        output_dir / "dcache_accesses_summary.csv",
        results,
        include_helios=plot_helios,
    )
    write_computation_log(
        output_dir / "dcache_accesses_computation_log.txt",
        results,
        include_helios=plot_helios,
    )
    plot_dcache_reduction_bars(results, output_dir, include_helios=plot_helios)

    rfp_avg = sum(r.rfp_reduction_pct for r in results) / len(results)
    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    if plot_helios:
        helios_values = [
            r.helios_reduction_pct for r in results if r.helios_reduction_pct is not None
        ]
        if helios_values:
            helios_avg = sum(helios_values) / len(helios_values)
            print(f"  Helios mean reduction:       {helios_avg:.2f}%")
    print(f"  RFP mean reduction:          {rfp_avg:.2f}%")
    print(f"  I-Fuse mean reduction:       {ifuse_avg:.2f}%")
    print(f"  Ideal fusion mean reduction: {ideal_avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'dcache_accesses.png'}")
    print(f"  - {output_dir / 'dcache_accesses.pdf'}")
    print(f"  - {output_dir / 'dcache_accesses.eps'}")
    print(f"  - {output_dir / 'dcache_accesses_summary.csv'}")
    print(f"  - {output_dir / 'dcache_accesses_computation_log.txt'}")


if __name__ == "__main__":
    main()
