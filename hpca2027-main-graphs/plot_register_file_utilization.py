#!/usr/bin/env python3
"""Simpoint-weighted physical GP register file utilization for baseline vs I-Fuse.

Reads ifuse.stat.0.csv counters sampled on every rename when Scarab is built with
the runtime-ifuse register-pressure hooks:
  avg_gp_util_pct = IFUSE_GP_REG_UTIL_PCT_TOTAL_count
                    / IFUSE_GP_REG_OCCUPIED_OBSERVATIONS_count
  avg_gp_regs_occupied = IFUSE_GP_REG_OCCUPIED_TOTAL_count
                         / IFUSE_GP_REG_OCCUPIED_OBSERVATIONS_count
  avg_extra_regs = IFUSE_EXTRA_REG_IN_USE_TOTAL_count
                   / IFUSE_EXTRA_REG_IN_USE_OBSERVATIONS_count

Baseline simpoints must use the runtime-ifuse Scarab binary with fusion disabled,
e.g. --ifuse_runtime_training_enabled 0 (see json/hpca2027/baseline_ifuse.json).
The standard no-fusion baseline binary does not emit ifuse.stat.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_register_file_utilization.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --baseline-dir /users/deepmish/scarab/src/simulations/baseline-ifuse \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/register_file_utilization
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
    BAR_WIDTH,
    BASELINE_COLOR,
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_BASELINE_DIR,
    DEFAULT_BASELINE_IFUSE_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_REGISTER_FILE_UTILIZATION_OUTPUT_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_FIGSIZE,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    _draw_app_x_tick_guides,
    _tight_x_limits,
    find_simpoint_dir,
    grouped_x_positions,
    load_simpoint_trace_weights,
    order_workloads_by_group,
    register_noto_serif,
    rename_workload,
)
import plot_ipc as _plot_ipc  # noqa: E402

IFUSE_STAT_FILE = "ifuse.stat.0.csv"
CORE_STAT_FILE = "core.stat.0.csv"
GP_UTIL_NUM = "IFUSE_GP_REG_UTIL_PCT_TOTAL_count"
GP_OBS = "IFUSE_GP_REG_OCCUPIED_OBSERVATIONS_count"
GP_OCC_NUM = "IFUSE_GP_REG_OCCUPIED_TOTAL_count"
EXTRA_NUM = "IFUSE_EXTRA_REG_IN_USE_TOTAL_count"
EXTRA_OBS = "IFUSE_EXTRA_REG_IN_USE_OBSERVATIONS_count"
GP_PEAK = "IFUSE_GP_REG_OCCUPIED_PEAK_total_count"
EXTRA_PEAK = "IFUSE_EXTRA_REG_IN_USE_PEAK_total_count"
# core.stat physical GP RF util (baseline / non-ifuse binaries)
RF_SAMPLES = "REG_FILE_UTIL_SAMPLES_count"
RF_INT_OCC = "REG_FILE_INT_OCCUPIED_SUM_count"
RF_INT_CAP = "REG_FILE_INT_PHYS_REGS_SUM_count"
REGISTER_BAR_WIDTH = 3.0  # thicker bars (2 schemes)
REGISTER_APP_STEP = 12.0  # keep a gap between apps with wider bars
REGISTER_AVERAGE_GAP = 3.5  # clear gap between last app and Average
REGISTER_FIGSIZE = (80.0, IPC_FIGSIZE[1])
REGISTER_BAR_EDGE_WIDTH = 6.0  # thicker bar/legend/spine outlines
REGISTER_AVERAGE_SEPARATOR_WIDTH = 8.0  # thicker Average divider
Y_LABEL = "Average physical register\nfile utilization (%)"
NO_FUSION_LABEL = "No fusion"
IFUSE_LABEL = "I-Fuse"


def _register_bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * REGISTER_BAR_WIDTH for i in range(n)]


def _apply_register_file_y_axis(ax) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    ax.set_ylim(0.0, 100.0)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.grid(True, axis="y", which="major", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)


def _apply_register_file_rcparams() -> None:
    import matplotlib.pyplot as plt

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


def _baseline_ifuse_legend_handles(*, include_baseline: bool = True) -> list:
    from matplotlib.patches import Patch

    handles = []
    if include_baseline:
        handles.append(
            Patch(
                facecolor=BASELINE_COLOR,
                edgecolor="black",
                linewidth=REGISTER_BAR_EDGE_WIDTH,
                label=NO_FUSION_LABEL,
            )
        )
    handles.append(
        Patch(
            facecolor=IFUSE_COLOR,
            edgecolor="black",
            linewidth=REGISTER_BAR_EDGE_WIDTH,
            label=IFUSE_LABEL,
        )
    )
    return handles


def _style_register_file_legend(ax, handles: list) -> None:
    legend = ax.legend(
        handles=handles,
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.96),
        bbox_transform=ax.transAxes,
        borderaxespad=0.0,
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        labelcolor="black",
        ncol=len(handles),
        handlelength=1.4,
        framealpha=1.0,
    )
    legend.get_frame().set_linewidth(REGISTER_BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)
    for text in legend.get_texts():
        text.set_fontfamily(FONT_FAMILY)
        text.set_color("black")


def _finalize_register_file_axes(ax) -> None:
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(REGISTER_BAR_EDGE_WIDTH)


def _save_register_file_figure(fig, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("register_file_utilization",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", pad_inches=0.05, dpi=300)


@dataclass
class IfuseOnlyRegisterFileResult:
    workload: str
    gp_util_pct: float
    avg_gp_regs_occupied: float
    avg_extra_regs: float
    peak_gp_regs: float
    peak_extra_regs: float
    trace_count: int


@dataclass
class RegisterFileResult:
    workload: str
    baseline_gp_util_pct: float
    ifuse_gp_util_pct: float
    baseline_avg_gp_regs_occupied: float
    ifuse_avg_gp_regs_occupied: float
    ifuse_avg_extra_regs: float
    baseline_peak_gp_regs: float
    ifuse_peak_gp_regs: float
    ifuse_peak_extra_regs: float
    trace_count: int


def ifuse_stat_available(sim_dir: Path | None) -> bool:
    return sim_dir is not None and (sim_dir / IFUSE_STAT_FILE).is_file()


def check_ifuse_stat_coverage(
    directories: dict[str, tuple[Path, str]],
    workloads: list[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    suite: str,
    subsuite: str,
    report_path: Path,
) -> set[str]:
    """Return workloads with complete ifuse.stat coverage across all configs."""
    complete_apps: set[str] = set()
    baseline_dir, baseline_config = directories["baseline"]

    with report_path.open("w") as rpt:
        rpt.write("=" * 100 + "\n")
        rpt.write("IFUSE.STAT COVERAGE REPORT\n")
        rpt.write("Reference: baseline — all other directories checked against it.\n")
        rpt.write("=" * 100 + "\n\n")

        for workload in workloads:
            rpt.write(f"\n{'=' * 80}\n")
            rpt.write(f"APP: {workload}\n")
            rpt.write(f"{'=' * 80}\n")

            reference_traces: set[str] = set()
            for wl, cid in sp_weights:
                if wl != workload:
                    continue
                sim_dir = find_simpoint_dir(
                    baseline_dir,
                    baseline_config,
                    wl,
                    cid,
                    suite=suite,
                    subsuite=subsuite,
                )
                if ifuse_stat_available(sim_dir):
                    reference_traces.add(cid)

            if not reference_traces:
                rpt.write("  !! No baseline ifuse.stat simpoints — skipping\n")
                continue

            rpt.write(f"  Reference traces from baseline: {sorted(reference_traces)}\n")
            app_complete = True
            for label, (exp_dir, config) in directories.items():
                rpt.write(f"\n  [{label}]  ({exp_dir})\n")
                found: set[str] = set()
                for cid in reference_traces:
                    sim_dir = find_simpoint_dir(
                        exp_dir,
                        config,
                        workload,
                        cid,
                        suite=suite,
                        subsuite=subsuite,
                    )
                    if ifuse_stat_available(sim_dir):
                        found.add(cid)
                missing = reference_traces - found
                if missing:
                    app_complete = False
                    rpt.write(f"    MISSING ({len(missing)}): {sorted(missing)}\n")
                else:
                    rpt.write(f"    OK  - All {len(reference_traces)} trace(s) present.\n")

            if app_complete:
                complete_apps.add(workload)
                rpt.write("\n  >> RESULT: COMPLETE\n")
            else:
                rpt.write("\n  >> RESULT: INCOMPLETE\n")

        rpt.write(f"\nCOMPLETE apps ({len(complete_apps)}): {sorted(complete_apps)}\n")

    print(f"\nifuse.stat coverage report written to: {report_path}")
    return complete_apps


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


def simpoint_register_metrics(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> tuple[float, float, float, float, float] | None:
    """Return (util_pct, avg_occ, avg_extra, gp_peak, extra_peak).

    Prefer ifuse.stat GP counters; fall back to core.stat REG_FILE_* occupancy
    (used by the standard no-fusion baseline binary).
    """
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None

    stat_csv = sim_dir / IFUSE_STAT_FILE
    gp_obs = stat_count_from_csv(stat_csv, GP_OBS)
    gp_util = stat_count_from_csv(stat_csv, GP_UTIL_NUM)
    gp_occ = stat_count_from_csv(stat_csv, GP_OCC_NUM)
    extra_obs = stat_count_from_csv(stat_csv, EXTRA_OBS)
    extra_total = stat_count_from_csv(stat_csv, EXTRA_NUM)
    gp_peak = stat_count_from_csv(stat_csv, GP_PEAK)
    extra_peak = stat_count_from_csv(stat_csv, EXTRA_PEAK)
    if (
        gp_obs is not None
        and gp_util is not None
        and gp_occ is not None
        and extra_obs is not None
        and extra_total is not None
        and gp_peak is not None
        and extra_peak is not None
        and gp_obs > 0
        and extra_obs > 0
    ):
        return (
            gp_util / gp_obs,
            gp_occ / gp_obs,
            extra_total / extra_obs,
            gp_peak,
            extra_peak,
        )

    # Fallback: core.stat physical int RF util
    #   util% = 100 * INT_OCCUPIED_SUM / INT_PHYS_REGS_SUM
    core_stat = sim_dir / CORE_STAT_FILE
    samples = stat_count_from_csv(core_stat, RF_SAMPLES)
    occ = stat_count_from_csv(core_stat, RF_INT_OCC)
    cap = stat_count_from_csv(core_stat, RF_INT_CAP)
    if samples is None or occ is None or cap is None or samples <= 0 or cap <= 0:
        return None
    return (
        100.0 * occ / cap,
        occ / samples,
        0.0,
        0.0,
        0.0,
    )


def compute_workload_register_util(
    workload: str,
    baseline_dir: Path,
    ifuse_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    baseline_config: str,
    ifuse_config: str,
    suite: str,
    subsuite: str,
) -> RegisterFileResult | None:
    weighted_baseline_util = 0.0
    weighted_ifuse_util = 0.0
    weighted_baseline_occ = 0.0
    weighted_ifuse_occ = 0.0
    weighted_extra = 0.0
    weighted_baseline_gp_peak = 0.0
    weighted_ifuse_gp_peak = 0.0
    weighted_extra_peak = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        baseline_metrics = simpoint_register_metrics(
            baseline_dir,
            baseline_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        ifuse_metrics = simpoint_register_metrics(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        if baseline_metrics is None or ifuse_metrics is None:
            continue

        baseline_util, baseline_occ, _baseline_extra, baseline_gp_peak, _baseline_extra_peak = (
            baseline_metrics
        )
        ifuse_util, ifuse_occ, extra_avg, ifuse_gp_peak, extra_peak = ifuse_metrics

        weighted_baseline_util += weight * baseline_util
        weighted_ifuse_util += weight * ifuse_util
        weighted_baseline_occ += weight * baseline_occ
        weighted_ifuse_occ += weight * ifuse_occ
        weighted_extra += weight * extra_avg
        weighted_baseline_gp_peak += weight * baseline_gp_peak
        weighted_ifuse_gp_peak += weight * ifuse_gp_peak
        weighted_extra_peak += weight * extra_peak
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0:
        return None

    return RegisterFileResult(
        workload=workload,
        baseline_gp_util_pct=weighted_baseline_util / weight_sum,
        ifuse_gp_util_pct=weighted_ifuse_util / weight_sum,
        baseline_avg_gp_regs_occupied=weighted_baseline_occ / weight_sum,
        ifuse_avg_gp_regs_occupied=weighted_ifuse_occ / weight_sum,
        ifuse_avg_extra_regs=weighted_extra / weight_sum,
        baseline_peak_gp_regs=weighted_baseline_gp_peak / weight_sum,
        ifuse_peak_gp_regs=weighted_ifuse_gp_peak / weight_sum,
        ifuse_peak_extra_regs=weighted_extra_peak / weight_sum,
        trace_count=trace_count,
    )


def compute_workload_ifuse_only_util(
    workload: str,
    ifuse_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    ifuse_config: str,
    suite: str,
    subsuite: str,
) -> IfuseOnlyRegisterFileResult | None:
    weighted_util = 0.0
    weighted_occ = 0.0
    weighted_extra = 0.0
    weighted_gp_peak = 0.0
    weighted_extra_peak = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        metrics = simpoint_register_metrics(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            suite=suite,
            subsuite=subsuite,
        )
        if metrics is None:
            continue
        gp_util, gp_occ, extra_avg, gp_peak, extra_peak = metrics
        weighted_util += weight * gp_util
        weighted_occ += weight * gp_occ
        weighted_extra += weight * extra_avg
        weighted_gp_peak += weight * gp_peak
        weighted_extra_peak += weight * extra_peak
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0:
        return None

    return IfuseOnlyRegisterFileResult(
        workload=workload,
        gp_util_pct=weighted_util / weight_sum,
        avg_gp_regs_occupied=weighted_occ / weight_sum,
        avg_extra_regs=weighted_extra / weight_sum,
        peak_gp_regs=weighted_gp_peak / weight_sum,
        peak_extra_regs=weighted_extra_peak / weight_sum,
        trace_count=trace_count,
    )


def derived_no_fusion_gp_util(
    ifuse_gp_util_pct: float,
    avg_gp_regs_occupied: float,
    avg_extra_regs: float,
) -> float:
    """Estimate no-fusion GP utilization by removing fusion extra registers."""
    if avg_gp_regs_occupied <= 0:
        return ifuse_gp_util_pct
    return ifuse_gp_util_pct * (1.0 - avg_extra_regs / avg_gp_regs_occupied)


def register_results_from_ifuse_only(
    ifuse_results: list[IfuseOnlyRegisterFileResult],
) -> list[RegisterFileResult]:
    paired: list[RegisterFileResult] = []
    for result in ifuse_results:
        baseline_util = derived_no_fusion_gp_util(
            result.gp_util_pct,
            result.avg_gp_regs_occupied,
            result.avg_extra_regs,
        )
        baseline_occ = max(0.0, result.avg_gp_regs_occupied - result.avg_extra_regs)
        paired.append(
            RegisterFileResult(
                workload=result.workload,
                baseline_gp_util_pct=baseline_util,
                ifuse_gp_util_pct=result.gp_util_pct,
                baseline_avg_gp_regs_occupied=baseline_occ,
                ifuse_avg_gp_regs_occupied=result.avg_gp_regs_occupied,
                ifuse_avg_extra_regs=result.avg_extra_regs,
                baseline_peak_gp_regs=result.peak_gp_regs,
                ifuse_peak_gp_regs=result.peak_gp_regs,
                ifuse_peak_extra_regs=result.peak_extra_regs,
                trace_count=result.trace_count,
            )
        )
    return paired


def check_ifuse_only_coverage(
    ifuse_dir: Path,
    ifuse_config: str,
    workloads: list[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    suite: str,
    subsuite: str,
    report_path: Path,
) -> set[str]:
    complete_apps: set[str] = set()
    with report_path.open("w") as rpt:
        rpt.write("=" * 100 + "\n")
        rpt.write("I-FUSE IFUSE.STAT COVERAGE REPORT\n")
        rpt.write("=" * 100 + "\n\n")
        for workload in workloads:
            found: set[str] = set()
            for wl, cid in sp_weights:
                if wl != workload:
                    continue
                sim_dir = find_simpoint_dir(
                    ifuse_dir,
                    ifuse_config,
                    wl,
                    cid,
                    suite=suite,
                    subsuite=subsuite,
                )
                if ifuse_stat_available(sim_dir):
                    found.add(cid)
            rpt.write(f"{workload}: {len(found)} simpoint(s) with ifuse.stat\n")
            if found:
                complete_apps.add(workload)
        rpt.write(f"\nCOMPLETE apps ({len(complete_apps)}): {sorted(complete_apps)}\n")
    print(f"\nifuse.stat coverage report written to: {report_path}")
    return complete_apps


def write_summary_csv(path: Path, results: list[RegisterFileResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "baseline_gp_util_pct",
                "ifuse_gp_util_pct",
                "baseline_avg_gp_regs_occupied",
                "ifuse_avg_gp_regs_occupied",
                "ifuse_avg_extra_regs",
                "baseline_peak_gp_regs",
                "ifuse_peak_gp_regs",
                "ifuse_peak_extra_regs",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "baseline_gp_util_pct": f"{result.baseline_gp_util_pct:.2f}",
                    "ifuse_gp_util_pct": f"{result.ifuse_gp_util_pct:.2f}",
                    "baseline_avg_gp_regs_occupied": (
                        f"{result.baseline_avg_gp_regs_occupied:.2f}"
                    ),
                    "ifuse_avg_gp_regs_occupied": (
                        f"{result.ifuse_avg_gp_regs_occupied:.2f}"
                    ),
                    "ifuse_avg_extra_regs": f"{result.ifuse_avg_extra_regs:.2f}",
                    "baseline_peak_gp_regs": f"{result.baseline_peak_gp_regs:.1f}",
                    "ifuse_peak_gp_regs": f"{result.ifuse_peak_gp_regs:.1f}",
                    "ifuse_peak_extra_regs": f"{result.ifuse_peak_extra_regs:.1f}",
                }
            )


def write_computation_log(
    path: Path,
    results: list[RegisterFileResult],
    *,
    derived_baseline: bool = False,
) -> None:
    with path.open("w") as fh:
        fh.write("Physical GP register file utilization (No fusion vs I-Fuse)\n")
        fh.write("=" * 80 + "\n")
        if derived_baseline:
            fh.write(
                "No fusion GP utilization estimated from I-Fuse stats by removing "
                "fusion extra registers:\n"
                "  no_fusion_util = ifuse_util * (1 - avg_extra_regs / avg_gp_regs_occupied)\n\n"
            )
        fh.write(
            "avg_gp_util_pct = weighted(IFUSE_GP_REG_UTIL_PCT_TOTAL_count "
            "/ IFUSE_GP_REG_OBSERVATIONS_count)\n"
        )
        fh.write(
            "avg_gp_regs_occupied = weighted(IFUSE_GP_REG_OCCUPIED_TOTAL_count "
            "/ IFUSE_GP_REG_OCCUPIED_OBSERVATIONS_count)\n"
        )
        fh.write(
            "avg_extra_regs (I-Fuse only) = weighted(IFUSE_EXTRA_REG_IN_USE_TOTAL_count "
            "/ IFUSE_EXTRA_REG_IN_USE_OBSERVATIONS_count)\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(
                f"  No fusion GP utilization: {result.baseline_gp_util_pct:.2f}%  "
                f"(avg occ {result.baseline_avg_gp_regs_occupied:.1f})\n"
            )
            fh.write(
                f"  I-Fuse GP utilization:   {result.ifuse_gp_util_pct:.2f}%  "
                f"(avg occ {result.ifuse_avg_gp_regs_occupied:.1f})\n"
            )
            fh.write(f"  I-Fuse extra fusion regs:  {result.ifuse_avg_extra_regs:.1f}\n")
            fh.write(
                f"  peak GP occupied: baseline {result.baseline_peak_gp_regs:.0f}, "
                f"I-Fuse {result.ifuse_peak_gp_regs:.0f}\n\n"
            )

        if results:
            baseline_avg = sum(r.baseline_gp_util_pct for r in results) / len(results)
            ifuse_avg = sum(r.ifuse_gp_util_pct for r in results) / len(results)
            extra_avg = sum(r.ifuse_avg_extra_regs for r in results) / len(results)
            fh.write(f"Arithmetic mean No fusion GP utilization: {baseline_avg:.2f}%\n")
            fh.write(f"Arithmetic mean I-Fuse GP utilization:   {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean I-Fuse extra registers:  {extra_avg:.2f}\n")


def plot_register_file_utilization_bars(
    results: list[RegisterFileResult],
    output_dir: Path,
) -> None:
    import matplotlib.pyplot as plt

    ordered_workloads = order_workloads_by_group([r.workload for r in results])
    results_by_workload = {r.workload: r for r in results}
    results = [results_by_workload[wl] for wl in ordered_workloads]

    baseline_pct = [r.baseline_gp_util_pct for r in results]
    ifuse_pct = [r.ifuse_gp_util_pct for r in results]
    baseline_pct.append(sum(baseline_pct) / len(baseline_pct))
    ifuse_pct.append(sum(ifuse_pct) / len(ifuse_pct))

    # Place apps with REGISTER_APP_STEP; recompute Average with register bar width.
    old_app_step = _plot_ipc.APP_STEP
    _plot_ipc.APP_STEP = REGISTER_APP_STEP
    try:
        _ordered, x_map, avg_x, separator_x = grouped_x_positions(
            ordered_workloads, n_series=2
        )
    finally:
        _plot_ipc.APP_STEP = old_app_step
    last_x = x_map[_ordered[-1]]
    cluster_half = (2 * REGISTER_BAR_WIDTH) / 2.0
    avg_x = last_x + cluster_half + REGISTER_AVERAGE_GAP + cluster_half
    separator_x = last_x + cluster_half + REGISTER_AVERAGE_GAP * 0.5
    display_apps = [rename_workload(wl) for wl in ordered_workloads] + ["Average"]
    x = [x_map[wl] for wl in ordered_workloads] + [avg_x]
    offsets = _register_bar_offsets(2)

    _apply_register_file_rcparams()
    fig, ax = plt.subplots(figsize=REGISTER_FIGSIZE)

    for offset, values, color in (
        (offsets[0], baseline_pct, BASELINE_COLOR),
        (offsets[1], ifuse_pct, IFUSE_COLOR),
    ):
        ax.bar(
            [i + offset for i in x],
            values,
            REGISTER_BAR_WIDTH,
            color=color,
            edgecolor="black",
            linewidth=REGISTER_BAR_EDGE_WIDTH,
            zorder=3,
        )

    if len(display_apps) > 1:
        ax.axvline(
            x=separator_x,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            alpha=1.0,
            linewidth=REGISTER_AVERAGE_SEPARATOR_WIDTH,
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
        Y_LABEL,
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    _apply_register_file_y_axis(ax)

    half_span = (2 * REGISTER_BAR_WIDTH) / 2.0
    # Extra side padding so bars are not flush with the plot edges.
    left_pad = 2.5
    right_pad = 2.5
    ax.set_xlim(x[0] - half_span - left_pad, x[-1] + half_span + right_pad)
    ax.margins(x=0)
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    _draw_app_x_tick_guides(ax, x)

    plt.subplots_adjust(top=0.72, bottom=0.30, left=0.12, right=0.98)
    _style_register_file_legend(ax, _baseline_ifuse_legend_handles())
    _finalize_register_file_axes(ax)
    _save_register_file_figure(fig, output_dir)
    plt.close(fig)


def plot_ifuse_only_register_bars(
    results: list[IfuseOnlyRegisterFileResult],
    output_dir: Path,
) -> None:
    plot_register_file_utilization_bars(register_results_from_ifuse_only(results), output_dir)


def write_ifuse_only_summary_csv(path: Path, results: list[IfuseOnlyRegisterFileResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "gp_util_pct",
                "avg_gp_regs_occupied",
                "avg_extra_regs",
                "peak_gp_regs",
                "peak_extra_regs",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "gp_util_pct": f"{result.gp_util_pct:.2f}",
                    "avg_gp_regs_occupied": f"{result.avg_gp_regs_occupied:.2f}",
                    "avg_extra_regs": f"{result.avg_extra_regs:.2f}",
                    "peak_gp_regs": f"{result.peak_gp_regs:.1f}",
                    "peak_extra_regs": f"{result.peak_extra_regs:.1f}",
                }
            )


def write_ifuse_only_computation_log(path: Path, results: list[IfuseOnlyRegisterFileResult]) -> None:
    with path.open("w") as fh:
        fh.write("Physical GP register file utilization (I-Fuse only; baseline pending)\n")
        fh.write("=" * 80 + "\n\n")
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  GP register file utilization: {result.gp_util_pct:.2f}%\n")
            fh.write(f"  avg GP registers occupied:    {result.avg_gp_regs_occupied:.1f}\n")
            fh.write(f"  avg extra fusion registers:   {result.avg_extra_regs:.1f}\n\n")
        if results:
            avg_util = sum(r.gp_util_pct for r in results) / len(results)
            avg_extra = sum(r.avg_extra_regs for r in results) / len(results)
            fh.write(f"Arithmetic mean GP utilization: {avg_util:.2f}%\n")
            fh.write(f"Arithmetic mean extra registers: {avg_extra:.2f}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted physical GP register file utilization "
            "for baseline vs I-Fuse."
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
        help=(
            "Baseline experiment directory (default: baseline-ifuse if present, "
            f"else {DEFAULT_BASELINE_DIR}; uses core.stat REG_FILE_* or ifuse.stat)"
        ),
    )
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Plot output directory "
            "(default: scarab/src/hpca2027-main-graphs-results/register_file_utilization)"
        ),
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    parser.add_argument(
        "--ifuse-only",
        action="store_true",
        help=(
            "Estimate No-fusion bars from I-Fuse extra-reg stats only "
            "(default: use baseline core.stat / ifuse.stat when available)."
        ),
    )
    args = parser.parse_args()
    register_noto_serif()

    if args.baseline_dir is not None:
        baseline_dir = args.baseline_dir
    elif DEFAULT_BASELINE_IFUSE_DIR.is_dir():
        baseline_dir = DEFAULT_BASELINE_IFUSE_DIR
    else:
        baseline_dir = DEFAULT_BASELINE_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_REGISTER_FILE_UTILIZATION_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)
    coverage_report = output_dir / "register_file_utilization_coverage_report.txt"

    ifuse_only = bool(args.ifuse_only)

    if ifuse_only:
        if not baseline_dir.is_dir():
            print(
                f"  note: {baseline_dir} not found; estimating No fusion bars from I-Fuse stats"
            )
        complete_workloads = check_ifuse_only_coverage(
            ifuse_dir,
            args.ifuse_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
            report_path=coverage_report,
        )
        if not complete_workloads:
            raise SystemExit(f"No workloads with I-Fuse ifuse.stat. See {coverage_report}.")

        print("Computing physical register file utilization (No fusion vs I-Fuse)...")
        print(f"  ifuse:  {ifuse_dir} (config={args.ifuse_config})")
        print(f"  output: {output_dir}")

        ifuse_results: list[IfuseOnlyRegisterFileResult] = []
        for workload in workloads:
            if workload not in complete_workloads:
                continue
            result = compute_workload_ifuse_only_util(
                workload,
                ifuse_dir,
                sp_weights,
                ifuse_config=args.ifuse_config,
                suite=DEFAULT_SUITE,
                subsuite=DEFAULT_SUBSUITE,
            )
            if result is None:
                continue
            ifuse_results.append(result)
            no_fusion = derived_no_fusion_gp_util(
                result.gp_util_pct,
                result.avg_gp_regs_occupied,
                result.avg_extra_regs,
            )
            print(
                f"  {workload:14s}  no_fusion={no_fusion:5.1f}%  "
                f"ifuse={result.gp_util_pct:5.1f}%  "
                f"extra_regs={result.avg_extra_regs:5.1f}  "
                f"(simpoints={result.trace_count})"
            )

        if not ifuse_results:
            raise SystemExit("No workloads with I-Fuse register utilization stats.")

        results = register_results_from_ifuse_only(ifuse_results)
        write_summary_csv(output_dir / "register_file_utilization_summary.csv", results)
        write_computation_log(
            output_dir / "register_file_utilization_computation_log.txt",
            results,
            derived_baseline=not baseline_dir.is_dir(),
        )
        plot_register_file_utilization_bars(results, output_dir)

        baseline_avg = sum(r.baseline_gp_util_pct for r in results) / len(results)
        ifuse_avg = sum(r.ifuse_gp_util_pct for r in results) / len(results)
        extra_avg = sum(r.ifuse_avg_extra_regs for r in results) / len(results)
        print("\nSummary:")
        print(f"  workloads plotted: {len(results)}")
        print(f"  Mean No fusion GP utilization: {baseline_avg:.2f}%")
        print(f"  Mean I-Fuse GP utilization:  {ifuse_avg:.2f}%")
        print(f"  Mean extra registers:        {extra_avg:.2f}")
        print("\nOutputs:")
        print(f"  - {output_dir / 'register_file_utilization.png'}")
        print(f"  - {output_dir / 'register_file_utilization.pdf'}")
        print(f"  - {output_dir / 'register_file_utilization.eps'}")
        print(f"  - {output_dir / 'register_file_utilization_summary.csv'}")
        print(f"  - {coverage_report}")
        return

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
    }
    # Prefer true side-by-side via compute_workload_register_util (ifuse.stat and/or
    # core.stat REG_FILE_*). Fall back to the old ifuse.stat-only coverage check
    # only when that finds complete pairs.
    print("Computing physical register file utilization (No fusion vs I-Fuse)...")
    print(f"  no fusion: {baseline_dir} (config={args.baseline_config})")
    print(f"  ifuse:    {ifuse_dir} (config={args.ifuse_config})")
    print(f"  output:   {output_dir}")

    results: list[RegisterFileResult] = []
    for workload in workloads:
        result = compute_workload_register_util(
            workload,
            baseline_dir,
            ifuse_dir,
            sp_weights,
            baseline_config=args.baseline_config,
            ifuse_config=args.ifuse_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            continue
        results.append(result)
        print(
            f"  {workload:14s}  no_fusion={result.baseline_gp_util_pct:5.1f}%  "
            f"ifuse={result.ifuse_gp_util_pct:5.1f}%  "
            f"extra_regs={result.ifuse_avg_extra_regs:5.1f}  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        # Last resort: old coverage path requiring ifuse.stat on both sides.
        complete_workloads = check_ifuse_stat_coverage(
            directories,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
            report_path=coverage_report,
        )
        raise SystemExit(
            "No workloads with paired baseline+I-Fuse register utilization stats. "
            f"See {coverage_report}. complete={sorted(complete_workloads)}"
        )

    with coverage_report.open("w") as rpt:
        rpt.write("Paired baseline+I-Fuse register-file utilization coverage\n")
        rpt.write(f"baseline: {baseline_dir}\n")
        rpt.write(f"ifuse:    {ifuse_dir}\n")
        rpt.write(f"workloads: {[r.workload for r in results]}\n")

    if not results:
        raise SystemExit("No workloads with No fusion and I-Fuse register utilization stats.")

    results_by_workload = {r.workload: r for r in results}
    results = [results_by_workload[wl] for wl in order_workloads_by_group(list(results_by_workload))]

    write_summary_csv(output_dir / "register_file_utilization_summary.csv", results)
    write_computation_log(output_dir / "register_file_utilization_computation_log.txt", results)
    plot_register_file_utilization_bars(results, output_dir)

    baseline_avg = sum(r.baseline_gp_util_pct for r in results) / len(results)
    ifuse_avg = sum(r.ifuse_gp_util_pct for r in results) / len(results)
    extra_avg = sum(r.ifuse_avg_extra_regs for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  Mean No fusion GP utilization: {baseline_avg:.2f}%")
    print(f"  Mean I-Fuse GP utilization:   {ifuse_avg:.2f}%")
    print(f"  Mean I-Fuse extra registers:  {extra_avg:.2f}")
    print("\nOutputs:")
    print(f"  - {output_dir / 'register_file_utilization.png'}")
    print(f"  - {output_dir / 'register_file_utilization.pdf'}")
    print(f"  - {output_dir / 'register_file_utilization.eps'}")
    print(f"  - {output_dir / 'register_file_utilization_summary.csv'}")
    print(f"  - {output_dir / 'register_file_utilization_computation_log.txt'}")
    print(f"  - {coverage_report}")


if __name__ == "__main__":
    main()
