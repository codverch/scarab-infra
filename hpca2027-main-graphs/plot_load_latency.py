#!/usr/bin/env python3
"""Simpoint-weighted load latency reduction for Helios, RFP, I-Fuse, and ideal fusion vs baseline.

Uses the total summed exec-minus-fetch latency per simpoint:
  LD_EXEC_MINUS_FETCH_LATENCY_count

Per workload (simpoint-weighted):
  reduction_pct = 100 * (weighted_baseline_total - weighted_config_total)
                  / weighted_baseline_total

Baseline, Helios, RFP, and I-Fuse read core.stat.0.csv. Ideal fusion reads
ideal_fusion.stat.0.csv; if the stored exec total is corrupt (>1e12), it is
estimated as LD_RETIRE * (baseline_exec / baseline_retire) for that simpoint.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_load_latency.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --helios-dir /users/deepmish/scarab/src/simulations/helios \
  --rfp-dir /users/deepmish/scarab/src/simulations/rfp \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse/tt256_thresh_1000 \
  --ifuse-config datacenter \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/load_latency
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
    ARROW_THRESHOLD,
    AVERAGE_SEPARATOR_COLOR,
    BAR_EDGE_WIDTH,
    BAR_WIDTH,
    DEFAULT_BASELINE_CONFIG,
    DEFAULT_BASELINE_DIR,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_LOAD_LATENCY_OUTPUT_DIR,
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
    IPC_AXIS_FONT,
    IPC_AXIS_LABEL_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
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

SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ideal", "Ideal fusion", IDEAL_FUSION_COLOR),
)


@dataclass
class LoadLatencyResult:
    workload: str
    baseline_exec_latency: float
    helios_exec_latency: float | None
    rfp_exec_latency: float | None
    ifuse_exec_latency: float
    ideal_exec_latency: float
    helios_reduction_pct: float | None
    rfp_reduction_pct: float | None
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
        if retire_total is None or baseline_retire <= 0 or baseline_exec <= 0:
            return None
        exec_total = retire_total * (baseline_exec / baseline_retire)
    return exec_total


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
        if baseline_exec is None or baseline_retire is None:
            return None
        return ideal_exec_latency_total(
            sim_dir / IDEAL_STAT_FILE,
            baseline_exec=baseline_exec,
            baseline_retire=baseline_retire,
        )

    return stat_count_from_csv(sim_dir / CORE_STAT_FILE, LOAD_EXEC_LATENCY_STAT)


def scheme_stats_available(
    experiment_dir: Path,
    config: str,
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
            if (
                simpoint_exec_latency_total(
                    experiment_dir,
                    config,
                    workload,
                    cluster_id,
                    use_ideal_stat=False,
                    suite=suite,
                    subsuite=subsuite,
                )
                is not None
            ):
                return True
    return False


def _reduction_pct(weighted_baseline: float, weighted_config: float) -> float:
    return 100.0 * (weighted_baseline - weighted_config) / weighted_baseline


def compute_workload_latency(
    workload: str,
    baseline_dir: Path,
    helios_dir: Path | None,
    rfp_dir: Path | None,
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
    include_rfp: bool,
    suite: str,
    subsuite: str,
) -> LoadLatencyResult | None:
    weighted_baseline = 0.0
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weighted_baseline_helios = 0.0
    weighted_helios = 0.0
    weighted_baseline_rfp = 0.0
    weighted_rfp = 0.0
    weight_sum = 0.0
    trace_count = 0
    helios_trace_count = 0
    rfp_trace_count = 0

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

        if include_helios and helios_dir is not None:
            helios_val = simpoint_exec_latency_total(
                helios_dir,
                helios_config,
                workload,
                cluster_id,
                use_ideal_stat=False,
                suite=suite,
                subsuite=subsuite,
            )
            if helios_val is not None:
                weighted_baseline_helios += weight * baseline_val
                weighted_helios += weight * helios_val
                helios_trace_count += 1

        if include_rfp and rfp_dir is not None:
            rfp_val = simpoint_exec_latency_total(
                rfp_dir,
                rfp_config,
                workload,
                cluster_id,
                use_ideal_stat=False,
                suite=suite,
                subsuite=subsuite,
            )
            if rfp_val is not None:
                weighted_baseline_rfp += weight * baseline_val
                weighted_rfp += weight * rfp_val
                rfp_trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_baseline <= 0:
        return None

    helios_reduction: float | None = None
    helios_exec: float | None = None
    if helios_trace_count > 0 and weighted_baseline_helios > 0:
        helios_exec = weighted_helios
        helios_reduction = _reduction_pct(weighted_baseline_helios, weighted_helios)

    rfp_reduction: float | None = None
    rfp_exec: float | None = None
    if rfp_trace_count > 0 and weighted_baseline_rfp > 0:
        rfp_exec = weighted_rfp
        rfp_reduction = _reduction_pct(weighted_baseline_rfp, weighted_rfp)

    return LoadLatencyResult(
        workload=workload,
        baseline_exec_latency=weighted_baseline,
        helios_exec_latency=helios_exec,
        rfp_exec_latency=rfp_exec,
        ifuse_exec_latency=weighted_ifuse,
        ideal_exec_latency=weighted_ideal,
        helios_reduction_pct=helios_reduction,
        rfp_reduction_pct=rfp_reduction,
        ifuse_reduction_pct=_reduction_pct(weighted_baseline, weighted_ifuse),
        ideal_reduction_pct=_reduction_pct(weighted_baseline, weighted_ideal),
        trace_count=trace_count,
        helios_trace_count=helios_trace_count,
        rfp_trace_count=rfp_trace_count,
    )


def write_summary_csv(
    path: Path,
    results: list[LoadLatencyResult],
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_baseline_exec_latency",
    ]
    if include_helios:
        fieldnames.extend(
            ["helios_trace_count", "weighted_helios_exec_latency", "helios_reduction_pct"]
        )
    if include_rfp:
        fieldnames.extend(["rfp_trace_count", "weighted_rfp_exec_latency", "rfp_reduction_pct"])
    fieldnames.extend(
        [
            "weighted_ifuse_exec_latency",
            "weighted_ideal_exec_latency",
            "ifuse_reduction_pct",
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
                "weighted_baseline_exec_latency": f"{result.baseline_exec_latency:.1f}",
                "weighted_ifuse_exec_latency": f"{result.ifuse_exec_latency:.1f}",
                "weighted_ideal_exec_latency": f"{result.ideal_exec_latency:.1f}",
                "ifuse_reduction_pct": f"{result.ifuse_reduction_pct:.2f}",
                "ideal_reduction_pct": f"{result.ideal_reduction_pct:.2f}",
            }
            if include_helios:
                row.update(
                    {
                        "helios_trace_count": result.helios_trace_count,
                        "weighted_helios_exec_latency": (
                            f"{result.helios_exec_latency:.1f}"
                            if result.helios_exec_latency is not None
                            else ""
                        ),
                        "helios_reduction_pct": (
                            f"{result.helios_reduction_pct:.2f}"
                            if result.helios_reduction_pct is not None
                            else ""
                        ),
                    }
                )
            if include_rfp:
                row.update(
                    {
                        "rfp_trace_count": result.rfp_trace_count,
                        "weighted_rfp_exec_latency": (
                            f"{result.rfp_exec_latency:.1f}"
                            if result.rfp_exec_latency is not None
                            else ""
                        ),
                        "rfp_reduction_pct": (
                            f"{result.rfp_reduction_pct:.2f}"
                            if result.rfp_reduction_pct is not None
                            else ""
                        ),
                    }
                )
            writer.writerow(row)


def write_computation_log(
    path: Path,
    results: list[LoadLatencyResult],
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    with path.open("w") as fh:
        fh.write("Load latency reduction (LD_EXEC_MINUS_FETCH_LATENCY_count)\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "reduction_pct = 100 * (weighted_baseline_total - weighted_config_total) "
            "/ weighted_baseline_total\n"
        )
        fh.write(
            "LD_EXEC_MINUS_FETCH_LATENCY: baseline/Helios/RFP/I-Fuse from core.stat.0.csv; "
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
            if include_helios and result.helios_reduction_pct is not None:
                fh.write(
                    f"  weighted helios exec-fetch total:   {result.helios_exec_latency:.1f}  "
                    f"({result.helios_reduction_pct:.2f}% reduction, "
                    f"{result.helios_trace_count} simpoints)\n"
                )
            elif include_helios:
                fh.write("  helios: n/a\n")
            if include_rfp and result.rfp_reduction_pct is not None:
                fh.write(
                    f"  weighted rfp exec-fetch total:      {result.rfp_exec_latency:.1f}  "
                    f"({result.rfp_reduction_pct:.2f}% reduction, "
                    f"{result.rfp_trace_count} simpoints)\n"
                )
            elif include_rfp:
                fh.write("  rfp: n/a\n")
            fh.write(
                f"  weighted ifuse exec-fetch total:    {result.ifuse_exec_latency:.1f}  "
                f"({result.ifuse_reduction_pct:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted ideal exec-fetch total:    {result.ideal_exec_latency:.1f}  "
                f"({result.ideal_reduction_pct:.2f}% reduction)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse reduction:  {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal reduction:   {ideal_avg:.2f}%\n")
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
            if include_rfp:
                rfp_vals = [
                    r.rfp_reduction_pct for r in results if r.rfp_reduction_pct is not None
                ]
                if rfp_vals:
                    fh.write(
                        f"Arithmetic mean RFP reduction:     "
                        f"{sum(rfp_vals) / len(rfp_vals):.2f}%\n"
                    )


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]


def _legend_handles(*, include_helios: bool, include_rfp: bool) -> list:
    from matplotlib.patches import Patch

    handles = []
    for key, label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        if key == "rfp" and not include_rfp:
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


def plot_load_latency_reduction_bars(
    results: list[LoadLatencyResult],
    output_dir: Path,
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    import matplotlib.pyplot as plt

    active_series = []
    for key, _label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        if key == "rfp" and not include_rfp:
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

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    offsets = _bar_offsets(len(active_series))

    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "font.size": IPC_AXIS_FONT,
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
            "legend.fontsize": IPC_AXIS_FONT,
        }
    )
    fig, ax = plt.subplots(figsize=(22, 6))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (_name, values, color) in zip(offsets, active_series):
        edge_colors = ["red" if (not math.isnan(val) and val < 0) else "black" for val in values]
        edge_widths = [
            2.5 if (not math.isnan(val) and val < 0) else BAR_EDGE_WIDTH for val in values
        ]
        ax.bar(
            [i + offset for i in x],
            [0.0 if math.isnan(val) else val for val in values],
            BAR_WIDTH,
            color=color,
            edgecolor=edge_colors,
            linewidth=edge_widths,
            zorder=3,
        )

    for offset, (_name, values, color) in zip(offsets, active_series):
        for i, val in enumerate(values):
            if math.isnan(val) or not (0 <= val < ARROW_THRESHOLD):
                continue
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
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    ax.set_ylabel(
        "Load latency reduction (%)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )

    all_values = [v for _k, values, _c in active_series for v in values if not math.isnan(v)]
    ymin = min(0.0, min(all_values)) if all_values else 0.0
    ymax = max(all_values) if all_values else 100.0
    ax.set_ylim(ymin, ymax * 1.12 + 2.0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    legend = ax.legend(
        handles=_legend_handles(include_helios=include_helios, include_rfp=include_rfp),
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="upper left",
        fontsize=IPC_AXIS_FONT,
        edgecolor="black",
        ncol=len(active_series),
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
        framealpha=1.0,
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
            "Plot simpoint-weighted load latency reduction for Helios, RFP, "
            "I-Fuse, and ideal fusion vs baseline."
        )
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--baseline-dir", type=Path, default=None)
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
    parser.add_argument(
        "--include-rfp",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Plot RFP bars (default: on)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
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

    baseline_dir = args.baseline_dir or DEFAULT_BASELINE_DIR
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_LOAD_LATENCY_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    plot_helios = False
    if args.include_helios:
        plot_helios = scheme_stats_available(
            helios_dir,
            args.helios_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_helios:
            print(f"Helios skipped: missing load latency stats in {helios_dir}")

    plot_rfp = False
    if args.include_rfp:
        plot_rfp = scheme_stats_available(
            rfp_dir,
            args.rfp_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_rfp:
            print(f"RFP skipped: missing load latency stats in {rfp_dir}")

    print("Computing load latency reductions (LD_EXEC_MINUS_FETCH_LATENCY)...")
    print(f"  baseline:     {baseline_dir} (config={args.baseline_config})")
    if plot_helios:
        print(f"  helios:       {helios_dir} (config={args.helios_config})")
    if plot_rfp:
        print(f"  rfp:          {rfp_dir} (config={args.rfp_config})")
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
        optional_configs={"helios", "rfp"},
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
            helios_dir if plot_helios else None,
            rfp_dir if plot_rfp else None,
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
            include_rfp=plot_rfp,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing load latency stats")
            continue
        results.append(result)
        parts = [f"ifuse={result.ifuse_reduction_pct:5.2f}%", f"ideal={result.ideal_reduction_pct:5.2f}%"]
        if result.helios_reduction_pct is not None:
            parts.insert(0, f"helios={result.helios_reduction_pct:5.2f}%")
        if result.rfp_reduction_pct is not None:
            idx = 1 if result.helios_reduction_pct is not None else 0
            parts.insert(idx, f"rfp={result.rfp_reduction_pct:5.2f}%")
        print(f"  {workload:14s}  {'  '.join(parts)}  (simpoints={result.trace_count})")

    if not results:
        raise SystemExit("No workloads with complete load latency data.")

    write_summary_csv(
        output_dir / "load_latency_summary.csv",
        results,
        include_helios=plot_helios,
        include_rfp=plot_rfp,
    )
    write_computation_log(
        output_dir / "load_latency_computation_log.txt",
        results,
        include_helios=plot_helios,
        include_rfp=plot_rfp,
    )
    plot_load_latency_reduction_bars(
        results,
        output_dir,
        include_helios=plot_helios,
        include_rfp=plot_rfp,
    )

    ifuse_avg = sum(r.ifuse_reduction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_reduction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    if plot_helios:
        helios_vals = [r.helios_reduction_pct for r in results if r.helios_reduction_pct is not None]
        if helios_vals:
            print(f"  Helios mean reduction:       {sum(helios_vals) / len(helios_vals):.2f}%")
    if plot_rfp:
        rfp_vals = [r.rfp_reduction_pct for r in results if r.rfp_reduction_pct is not None]
        if rfp_vals:
            print(f"  RFP mean reduction:          {sum(rfp_vals) / len(rfp_vals):.2f}%")
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
