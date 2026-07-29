#!/usr/bin/env python3
"""Simpoint-weighted fraction of on-path memory loads covered by each scheme.

Per workload (simpoint-weighted):
  Helios:              100 * 2 * HELIOS_FUSIONS_COMMITTED / ONPATH_MEM_LOADS
  Register file prefetching: 100 * RFP_PREFETCH_USEFUL / ONPATH_MEM_LOADS
  I-Fuse:              100 * 2 * IFUSE_FUSED_LOADS / ONPATH_MEM_LOADS
  Ideal fusion:        100 * IDEAL_FUSION_LOADS_PARTICIPATED / ONPATH_MEM_LOADS

Fusion schemes count both loads in each fused pair (LD1 + LD2), so fused-pair
counts are scaled by 2. Ideal uses IDEAL_FUSION_LOADS_PARTICIPATED, which is
already LD1+LD2. RFP covers one load per useful prefetch, so it is not scaled.

ONPATH_MEM_LOADS is read from ideal-fusion simpoints and used as the shared
denominator for every technique.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_fusion_fraction.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --helios-dir /users/deepmish/scarab/src/simulations/helios \
  --rfp-dir /users/deepmish/scarab/src/simulations/rfp \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --ideal-fusion-dir /users/deepmish/scarab/src/simulations/ideal-fusion \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/fusion_fraction
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
    DEFAULT_FUSION_FRACTION_OUTPUT_DIR,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
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
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    LEGEND_X_OFFSET,
    RFP_COLOR,
    SIMPOINT_WORKLOADS,
    _apply_ipc_plot_style,
    _bar_offsets,
    _draw_app_x_tick_guides,
    _tight_x_limits,
    find_simpoint_dir,
    grouped_x_positions,
    load_simpoint_trace_weights,
    order_workloads_by_group,
    rename_workload,
)

HELIOS_FUSED_STAT = "HELIOS_FUSIONS_COMMITTED_count"
RFP_USEFUL_STAT = "RFP_PREFETCH_USEFUL_count"
IFUSE_FUSED_STAT = "IFUSE_FUSED_LOADS_count"
IDEAL_LOADS_PARTICIPATED_STAT = "IDEAL_FUSION_LOADS_PARTICIPATED_count"
ONPATH_MEM_LOADS_STAT = "ONPATH_MEM_LOADS_count"
PERIODIC_INSTRUCTIONS_STAT = "Periodic_Instructions"
# Fusion covers both loads in a pair (LD1 + LD2). RFP is already one load per event.
LOADS_PER_FUSION_PAIR = 2
MEASUREMENT_WINDOW_TOLERANCE = 0.05

SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ideal", "Ideal fusion", IDEAL_FUSION_COLOR),
)


@dataclass
class FusionFractionResult:
    workload: str
    helios_loads_participated: float
    rfp_useful_prefetches: float
    ifuse_loads_participated: float
    ideal_loads_participated: float
    onpath_mem_loads: float
    helios_fraction_pct: float | None
    rfp_fraction_pct: float | None
    ifuse_fraction_pct: float
    ideal_fraction_pct: float
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


def simpoint_stat(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    stat_file: str,
    stat_name: str,
    suite: str,
    subsuite: str,
) -> float | None:
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None
    return stat_count_from_csv(sim_dir / stat_file, stat_name)


def periodic_instructions(sim_dir: Path | None) -> float | None:
    if sim_dir is None:
        return None
    return stat_count_from_csv(sim_dir / "core.stat.0.csv", PERIODIC_INSTRUCTIONS_STAT)


def scale_to_measurement_window(
    count: float,
    scheme_periodic: float | None,
    reference_periodic: float | None,
) -> float:
    if (
        scheme_periodic is None
        or reference_periodic is None
        or scheme_periodic <= 0
        or reference_periodic <= 0
    ):
        return count
    if abs(scheme_periodic - reference_periodic) / reference_periodic <= MEASUREMENT_WINDOW_TOLERANCE:
        return count
    return count * (reference_periodic / scheme_periodic)


def helios_stats_available(
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
            if stat_count_from_csv(sim_dir / "core.stat.0.csv", HELIOS_FUSED_STAT) is not None:
                return True
    return False


def rfp_stats_available(
    rfp_dir: Path,
    rfp_config: str,
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
                rfp_dir, rfp_config, workload, cluster_id, suite=suite, subsuite=subsuite
            )
            if sim_dir is None:
                continue
            if stat_count_from_csv(sim_dir / "rfp.stat.0.csv", RFP_USEFUL_STAT) is not None:
                return True
    return False


def compute_workload_fraction(
    workload: str,
    helios_dir: Path | None,
    rfp_dir: Path | None,
    ifuse_dir: Path,
    ideal_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    helios_config: str,
    rfp_config: str,
    ifuse_config: str,
    ideal_config: str,
    include_helios: bool,
    include_rfp: bool,
    suite: str,
    subsuite: str,
) -> FusionFractionResult | None:
    weighted_helios = 0.0
    weighted_rfp = 0.0
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weighted_onpath = 0.0
    weight_sum = 0.0
    trace_count = 0
    helios_trace_count = 0
    rfp_trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue

        ideal_sim = find_simpoint_dir(
            ideal_dir, ideal_config, workload, cluster_id, suite=suite, subsuite=subsuite
        )
        ideal_participated = simpoint_stat(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file="ideal_fusion.stat.0.csv",
            stat_name=IDEAL_LOADS_PARTICIPATED_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        onpath_loads = simpoint_stat(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file="ideal_fusion.stat.0.csv",
            stat_name=ONPATH_MEM_LOADS_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        ifuse_fused = simpoint_stat(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file="ifuse.stat.0.csv",
            stat_name=IFUSE_FUSED_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        if ideal_participated is None or onpath_loads is None or ifuse_fused is None or onpath_loads <= 0:
            continue

        reference_periodic = periodic_instructions(ideal_sim)

        helios_participated: float | None = None
        if include_helios and helios_dir is not None:
            helios_sim = find_simpoint_dir(
                helios_dir, helios_config, workload, cluster_id, suite=suite, subsuite=subsuite
            )
            helios_fused = simpoint_stat(
                helios_dir,
                helios_config,
                workload,
                cluster_id,
                stat_file="core.stat.0.csv",
                stat_name=HELIOS_FUSED_STAT,
                suite=suite,
                subsuite=subsuite,
            )
            if helios_fused is not None:
                helios_fused = scale_to_measurement_window(
                    helios_fused,
                    periodic_instructions(helios_sim),
                    reference_periodic,
                )
                helios_participated = LOADS_PER_FUSION_PAIR * helios_fused
                weighted_helios += weight * helios_participated
                helios_trace_count += 1

        rfp_useful: float | None = None
        if include_rfp and rfp_dir is not None:
            rfp_useful = simpoint_stat(
                rfp_dir,
                rfp_config,
                workload,
                cluster_id,
                stat_file="rfp.stat.0.csv",
                stat_name=RFP_USEFUL_STAT,
                suite=suite,
                subsuite=subsuite,
            )
            if rfp_useful is not None:
                weighted_rfp += weight * rfp_useful
                rfp_trace_count += 1

        ifuse_participated = LOADS_PER_FUSION_PAIR * ifuse_fused
        weighted_ifuse += weight * ifuse_participated
        weighted_ideal += weight * ideal_participated
        weighted_onpath += weight * onpath_loads
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_onpath <= 0:
        return None

    helios_pct: float | None = None
    if helios_trace_count > 0:
        helios_pct = 100.0 * weighted_helios / weighted_onpath

    rfp_pct: float | None = None
    if rfp_trace_count > 0:
        rfp_pct = 100.0 * weighted_rfp / weighted_onpath

    return FusionFractionResult(
        workload=workload,
        helios_loads_participated=weighted_helios,
        rfp_useful_prefetches=weighted_rfp,
        ifuse_loads_participated=weighted_ifuse,
        ideal_loads_participated=weighted_ideal,
        onpath_mem_loads=weighted_onpath,
        helios_fraction_pct=helios_pct,
        rfp_fraction_pct=rfp_pct,
        ifuse_fraction_pct=100.0 * weighted_ifuse / weighted_onpath,
        ideal_fraction_pct=100.0 * weighted_ideal / weighted_onpath,
        trace_count=trace_count,
        helios_trace_count=helios_trace_count,
        rfp_trace_count=rfp_trace_count,
    )


def write_summary_csv(
    path: Path,
    results: list[FusionFractionResult],
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_onpath_mem_loads",
    ]
    if include_helios:
        fieldnames.extend(
            [
                "helios_trace_count",
                "weighted_helios_loads_participated",
                "helios_fraction_pct",
            ]
        )
    if include_rfp:
        fieldnames.extend(
            [
                "rfp_trace_count",
                "weighted_rfp_useful_prefetches",
                "rfp_fraction_pct",
            ]
        )
    fieldnames.extend(
        [
            "weighted_ifuse_loads_participated",
            "weighted_ideal_loads_participated",
            "ifuse_fraction_pct",
            "ideal_fraction_pct",
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
                "weighted_onpath_mem_loads": f"{result.onpath_mem_loads:.1f}",
                "weighted_ifuse_loads_participated": f"{result.ifuse_loads_participated:.1f}",
                "weighted_ideal_loads_participated": f"{result.ideal_loads_participated:.1f}",
                "ifuse_fraction_pct": f"{result.ifuse_fraction_pct:.2f}",
                "ideal_fraction_pct": f"{result.ideal_fraction_pct:.2f}",
            }
            if include_helios:
                row.update(
                    {
                        "helios_trace_count": result.helios_trace_count,
                        "weighted_helios_loads_participated": f"{result.helios_loads_participated:.1f}",
                        "helios_fraction_pct": (
                            f"{result.helios_fraction_pct:.2f}"
                            if result.helios_fraction_pct is not None
                            else ""
                        ),
                    }
                )
            if include_rfp:
                row.update(
                    {
                        "rfp_trace_count": result.rfp_trace_count,
                        "weighted_rfp_useful_prefetches": f"{result.rfp_useful_prefetches:.1f}",
                        "rfp_fraction_pct": (
                            f"{result.rfp_fraction_pct:.2f}"
                            if result.rfp_fraction_pct is not None
                            else ""
                        ),
                    }
                )
            writer.writerow(row)


def write_computation_log(
    path: Path,
    results: list[FusionFractionResult],
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    with path.open("w") as fh:
        fh.write("Fraction of on-path memory loads covered\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "fraction_pct = 100 * weighted(loads covered or participating in fusion) "
            "/ weighted(ONPATH_MEM_LOADS_count)\n\n"
        )
        if include_helios:
            fh.write(
                f"Helios loads participating = 2 * {HELIOS_FUSED_STAT} "
                "(scaled to ideal Periodic_Instructions when windows differ)\n"
            )
        if include_rfp:
            fh.write(f"RFP useful prefetches = {RFP_USEFUL_STAT}\n")
        fh.write(
            "I-Fuse loads participating = 2 * IFUSE_FUSED_LOADS_count; "
            "ideal loads participating = IDEAL_FUSION_LOADS_PARTICIPATED_count "
            "(already LD1+LD2). RFP is 1 load per useful prefetch.\n"
        )
        fh.write("ONPATH_MEM_LOADS_count is read from ideal-fusion simpoints.\n\n")

        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted on-path mem loads: {result.onpath_mem_loads:.1f}\n")
            if include_helios and result.helios_fraction_pct is not None:
                fh.write(
                    f"  helios: {result.helios_loads_participated:.1f}  "
                    f"({result.helios_fraction_pct:.2f}%, {result.helios_trace_count} simpoints)\n"
                )
            if include_rfp and result.rfp_fraction_pct is not None:
                fh.write(
                    f"  rfp:    {result.rfp_useful_prefetches:.1f}  "
                    f"({result.rfp_fraction_pct:.2f}%, {result.rfp_trace_count} simpoints)\n"
                )
            fh.write(
                f"  ifuse:  {result.ifuse_loads_participated:.1f}  "
                f"({result.ifuse_fraction_pct:.2f}%)\n"
            )
            fh.write(
                f"  ideal:  {result.ideal_loads_participated:.1f}  "
                f"({result.ideal_fraction_pct:.2f}%)\n\n"
            )


def _legend_handles(
    *,
    include_helios: bool,
    include_rfp: bool,
) -> list:
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


def plot_fusion_fraction_bars(
    results: list[FusionFractionResult],
    output_dir: Path,
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    active_series = [
        entry
        for entry in SERIES
        if not (
            (entry[0] == "helios" and not include_helios)
            or (entry[0] == "rfp" and not include_rfp)
        )
    ]
    attr_by_key = {
        "helios": "helios_fraction_pct",
        "rfp": "rfp_fraction_pct",
        "ifuse": "ifuse_fraction_pct",
        "ideal": "ideal_fraction_pct",
    }

    workloads = [r.workload for r in results]
    ordered, x_map, avg_x, separator_x = grouped_x_positions(
        workloads, n_series=len(active_series)
    )
    result_by_wl = {r.workload: r for r in results}
    display_apps = [rename_workload(wl) for wl in ordered] + ["Average"]
    x_ticks = [x_map[wl] for wl in ordered] + [avg_x]
    offsets = _bar_offsets(len(active_series))

    series_values: dict[str, list[float]] = {}
    for key, _label, _color in active_series:
        values = []
        for wl in ordered:
            val = getattr(result_by_wl[wl], attr_by_key[key])
            values.append(float("nan") if val is None else float(val))
        finite = [v for v in values if not math.isnan(v)]
        avg = sum(finite) / len(finite) if finite else float("nan")
        values.append(avg)
        series_values[key] = values

    _apply_ipc_plot_style()
    fig_width = max(22.0, len(x_ticks) * APP_STEP * 1.15 + AVERAGE_GAP)
    fig, ax = plt.subplots(figsize=(fig_width, 6.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for (key, _label, color), offset in zip(active_series, offsets):
        values = series_values[key]
        bar_x = [x_map[wl] + offset for wl in ordered] + [avg_x + offset]
        ax.bar(
            bar_x,
            [0.0 if math.isnan(val) else val for val in values],
            BAR_WIDTH,
            color=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )

    if len(x_ticks) > 1:
        ax.axvline(
            x=separator_x,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            alpha=1.0,
            linewidth=AVERAGE_SEPARATOR_WIDTH,
            zorder=2,
        )

    ax.set_xticks(x_ticks)
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

    _tight_x_limits(ax, x_ticks[0], x_ticks[-1], n_bars=len(active_series))

    ax.set_ylabel(
        "Fraction of total on-path\nmemory loads\ncovered (%)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.set_ylim(0.0, 100.0)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)
    _draw_app_x_tick_guides(ax, x_ticks)

    x_min, x_max = ax.get_xlim()
    separator_x_frac = (separator_x - x_min) / (x_max - x_min) - LEGEND_X_OFFSET
    legend = ax.legend(
        handles=_legend_handles(include_helios=include_helios, include_rfp=include_rfp),
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="upper right",
        bbox_to_anchor=(separator_x_frac, 0.98),
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
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    plt.subplots_adjust(top=0.95, bottom=0.28, right=0.98)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("fusion_fraction",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", pad_inches=0.05, dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot fraction of on-path memory loads covered by Helios, RFP, I-Fuse, and ideal fusion."
    )
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
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
        help="Plot register-file prefetching bars (default: on)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument("--workloads-db", type=Path, default=DEFAULT_WORKLOADS_DB)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion")
    workloads = order_workloads_by_group(
        [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    )
    output_dir = args.output_dir or DEFAULT_FUSION_FRACTION_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(
        args.trace_root,
        workloads,
        workloads_db=args.workloads_db,
    )

    plot_helios = False
    if args.include_helios:
        plot_helios = helios_stats_available(
            helios_dir,
            args.helios_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_helios:
            print(f"Helios skipped: missing {HELIOS_FUSED_STAT} in {helios_dir}")

    plot_rfp = False
    if args.include_rfp:
        plot_rfp = rfp_stats_available(
            rfp_dir,
            args.rfp_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_rfp:
            print(f"RFP skipped: missing {RFP_USEFUL_STAT} in {rfp_dir}")

    print("Computing on-path memory load coverage fractions...")
    if plot_helios:
        print(f"  helios:       {helios_dir} (config={args.helios_config})")
    if plot_rfp:
        print(f"  rfp:          {rfp_dir} (config={args.rfp_config})")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    results: list[FusionFractionResult] = []
    for workload in workloads:
        result = compute_workload_fraction(
            workload,
            helios_dir if plot_helios else None,
            rfp_dir if plot_rfp else None,
            ifuse_dir,
            ideal_dir,
            sp_weights,
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
            print(f"  skip {workload}: missing ifuse/ideal-fusion stats")
            continue
        results.append(result)
        helios_str = (
            f"{result.helios_fraction_pct:5.2f}%"
            if result.helios_fraction_pct is not None
            else "  n/a"
        )
        rfp_str = (
            f"{result.rfp_fraction_pct:5.2f}%"
            if result.rfp_fraction_pct is not None
            else "  n/a"
        )
        print(
            f"  {workload:14s}  helios={helios_str}  rfp={rfp_str}  "
            f"ifuse={result.ifuse_fraction_pct:5.2f}%  ideal={result.ideal_fraction_pct:5.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete I-Fuse and ideal-fusion data.")

    write_summary_csv(
        output_dir / "fusion_fraction_summary.csv",
        results,
        include_helios=plot_helios,
        include_rfp=plot_rfp,
    )
    write_computation_log(
        output_dir / "fusion_fraction_computation_log.txt",
        results,
        include_helios=plot_helios,
        include_rfp=plot_rfp,
    )
    plot_fusion_fraction_bars(
        results,
        output_dir,
        include_helios=plot_helios,
        include_rfp=plot_rfp,
    )

    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    if plot_helios:
        helios_vals = [r.helios_fraction_pct for r in results if r.helios_fraction_pct is not None]
        if helios_vals:
            print(f"  Helios mean:       {sum(helios_vals) / len(helios_vals):.2f}%")
    if plot_rfp:
        rfp_vals = [r.rfp_fraction_pct for r in results if r.rfp_fraction_pct is not None]
        if rfp_vals:
            print(f"  RFP mean:          {sum(rfp_vals) / len(rfp_vals):.2f}%")
    print(f"  I-Fuse mean:       {sum(r.ifuse_fraction_pct for r in results) / len(results):.2f}%")
    print(f"  Ideal fusion mean: {sum(r.ideal_fraction_pct for r in results) / len(results):.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'fusion_fraction.png'}")
    print(f"  - {output_dir / 'fusion_fraction.pdf'}")
    print(f"  - {output_dir / 'fusion_fraction.eps'}")
    print(f"  - {output_dir / 'fusion_fraction_summary.csv'}")
    print(f"  - {output_dir / 'fusion_fraction_computation_log.txt'}")


if __name__ == "__main__":
    main()
