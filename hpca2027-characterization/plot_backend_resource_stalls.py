#!/usr/bin/env python3
"""Backend resource stall breakdown for baseline vs I-Fuse (simpoint-weighted).

Each bar stacks the percentage of cycles in which rename/allocation is blocked,
attributed to the backend resource that blocked it (core.stat.0.csv, ROI counts):

  - Register file full  = MAP_STAGE_STALL_ITSELF
        map stage cannot allocate physical registers; map returns before
        MAP_STAGE_STALLED is counted, so the two are mutually exclusive.
  - ROB / LQ / SQ full  = MAP_STAGE_STALLED (map blocked by allocation into the
        ROB/LSQ), split in proportion to FULL_WINDOW_STALL,
        LSQ_FULL_LOAD_QUEUE and LSQ_FULL_STORE_QUEUE. Any MAP_STAGE_STALLED
        cycles not covered by those counters (e.g. BAR_ISSUE serialization)
        are shown as "Other".

The scheduler (RS/IQ) is filled from the ROB in issue_queue.cc and never
blocks allocation directly; a full scheduler would surface as ROB-full cycles.
Execution-port contention is reported (not plotted) as ready-but-not-issued
ops per cycle (RS_OP_READY_NOT_ISSUED_TOTAL / NODE_CYCLE) in the summary CSV.

Simpoint weights come from the trace root (opt.p/opt.w, or selection.json for
memcached), which matches the simulated cluster ids; pass --workloads-db to use
workloads_db.json weights instead.

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-characterization/plot_backend_resource_stalls.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --trace-root /dev/shm/ifuse/simpoint_traces
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
from matplotlib.patches import Patch

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
MAIN_GRAPHS = SCARAB_INFRA_ROOT / "hpca2027-main-graphs"
if str(MAIN_GRAPHS) not in sys.path:
    sys.path.insert(0, str(MAIN_GRAPHS))

import plot_ipc  # noqa: E402
from plot_fusion_fraction import stat_count_from_csv  # noqa: E402
from plot_ipc import (  # noqa: E402
    DEFAULT_SCARAB_ROOT,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_FIGSIZE,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    register_noto_serif,
    rename_workload,
)

REG_FILE_COLOR = "#CF3054"  # red (same as Helios "Covered")
ROB_COLOR = "#D5D5D4"  # light gray
LQ_COLOR = IFUSE_COLOR  # green
SQ_COLOR = "#6E6E6E"  # dark gray
OTHER_COLOR = "#FFFFFF"

DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-characterization-results"
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "backend-resource-stalls"
OUTPUT_STEM = "backend-resource-stalls"

CORE_STAT = "core.stat.0.csv"
CYCLES_STAT = "NODE_CYCLE_count"
REG_FILE_STALL_STAT = "MAP_STAGE_STALL_ITSELF_count"
DOWNSTREAM_STALL_STAT = "MAP_STAGE_STALLED_count"
ROB_FULL_STAT = "FULL_WINDOW_STALL_count"
LQ_FULL_STAT = "LSQ_FULL_LOAD_QUEUE_count"
SQ_FULL_STAT = "LSQ_FULL_STORE_QUEUE_count"
PORT_CONFLICT_STAT = "RS_OP_READY_NOT_ISSUED_TOTAL_count"
STATS = (
    CYCLES_STAT,
    REG_FILE_STALL_STAT,
    DOWNSTREAM_STALL_STAT,
    ROB_FULL_STAT,
    LQ_FULL_STAT,
    SQ_FULL_STAT,
    PORT_CONFLICT_STAT,
)

# (config key, legend label, hatch) — left-to-right within each app.
CONFIGS: tuple[tuple[str, str, str], ...] = (
    ("baseline", "Baseline", ""),
    ("ifuse", "I-Fuse", "//"),
)

# Match the Helios coverage-cause canvas, with two bars per app.
BAR_WIDTH = 3.6
BAR_OFFSET = 2.0
APP_STEP = 10.0
AVERAGE_GAP = 2.4
FIGSIZE = (IPC_FIGSIZE[0], IPC_FIGSIZE[1] + 6.0)
BAR_EDGE_WIDTH = 7.0
HATCH_WIDTH = 4.0
AVERAGE_SEPARATOR_COLOR = "#2A2A2A"
AVERAGE_SEPARATOR_WIDTH = 10.0
AXIS_FONT = IPC_TICK_FONT
AXIS_LABEL_FONT = IPC_AXIS_LABEL_FONT
LEGEND_FONT = 90
Y_LABEL_PAD = 28
Y_MAX = 70.0
OUTPUT_DPI = 300
Y_AXIS_LABEL = "Allocation stall\ncycles by backend\nresource (%)"

# (field, color) — bottom-to-top stack order.
BREAKDOWN_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("reg_file_pct", REG_FILE_COLOR),
    ("rob_pct", ROB_COLOR),
    ("lq_pct", LQ_COLOR),
    ("sq_pct", SQ_COLOR),
    ("other_pct", OTHER_COLOR),
)

BREAKDOWN_CATEGORIES: dict[str, str] = {
    "reg_file_pct": "Register file full",
    "rob_pct": "ROB full",
    "lq_pct": "Load queue full",
    "sq_pct": "Store queue full",
    "other_pct": "Other",
}


def _apply_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": plot_ipc.FONT_FAMILY,
            "font.serif": [plot_ipc.FONT_FAMILY, "DejaVu Serif", "serif"],
            "text.color": "black",
            "axes.labelcolor": "black",
            "axes.labelsize": AXIS_LABEL_FONT,
            "xtick.color": "black",
            "ytick.color": "black",
            "xtick.labelsize": AXIS_FONT,
            "ytick.labelsize": AXIS_FONT,
            "legend.fontsize": LEGEND_FONT,
            "hatch.linewidth": HATCH_WIDTH,
        }
    )


@dataclass
class StallBreakdown:
    reg_file_pct: float
    rob_pct: float
    lq_pct: float
    sq_pct: float
    other_pct: float
    port_conflicts_per_cycle: float

    @property
    def total_pct(self) -> float:
        return sum(getattr(self, field) for field, _ in BREAKDOWN_SEGMENTS)


@dataclass
class WorkloadResult:
    workload: str
    trace_count: int
    breakdowns: dict[str, StallBreakdown]


def breakdown_from_counts(counts: dict[str, float]) -> StallBreakdown | None:
    cycles = counts[CYCLES_STAT]
    if cycles <= 0:
        return None

    downstream = counts[DOWNSTREAM_STALL_STAT]
    rob, lq, sq = counts[ROB_FULL_STAT], counts[LQ_FULL_STAT], counts[SQ_FULL_STAT]
    attributed = rob + lq + sq
    # ROB/LSQ-full counters fire once per blocked node-fill cycle; they can also
    # fire while map is register-stalled, so scale them onto MAP_STAGE_STALLED.
    scale = min(1.0, downstream / attributed) if attributed > 0 else 0.0
    other = max(0.0, downstream - attributed)

    def pct(value: float) -> float:
        return 100.0 * value / cycles

    return StallBreakdown(
        reg_file_pct=pct(counts[REG_FILE_STALL_STAT]),
        rob_pct=pct(rob * scale),
        lq_pct=pct(lq * scale),
        sq_pct=pct(sq * scale),
        other_pct=pct(other),
        port_conflicts_per_cycle=counts[PORT_CONFLICT_STAT] / cycles,
    )


def workload_breakdowns(
    workload: str,
    sp_weights: dict[tuple[str, str], float],
    config_dirs: dict[str, Path],
    *,
    suite: str,
    subsuite: str,
) -> WorkloadResult | None:
    weighted = {cfg: {stat: 0.0 for stat in STATS} for cfg in config_dirs}
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        sim_dirs = {
            cfg: find_simpoint_dir(
                path, cfg, workload, cluster_id, suite=suite, subsuite=subsuite
            )
            for cfg, path in config_dirs.items()
        }
        if any(sim_dir is None for sim_dir in sim_dirs.values()):
            continue

        values: dict[str, dict[str, float]] = {}
        for cfg, sim_dir in sim_dirs.items():
            values[cfg] = {}
            for stat in STATS:
                value = stat_count_from_csv(sim_dir / CORE_STAT, stat)
                if value is None:
                    break
                values[cfg][stat] = value
        if any(len(v) != len(STATS) for v in values.values()):
            continue

        for cfg in config_dirs:
            for stat in STATS:
                weighted[cfg][stat] += weight * values[cfg][stat]
        trace_count += 1

    if trace_count == 0:
        return None

    breakdowns: dict[str, StallBreakdown] = {}
    for cfg in config_dirs:
        breakdown = breakdown_from_counts(weighted[cfg])
        if breakdown is None:
            return None
        breakdowns[cfg] = breakdown

    return WorkloadResult(
        workload=workload,
        trace_count=trace_count,
        breakdowns=breakdowns,
    )


def average_result(results: list[WorkloadResult]) -> WorkloadResult:
    def mean(cfg: str, field: str) -> float:
        return sum(getattr(r.breakdowns[cfg], field) for r in results) / len(results)

    fields = [field for field, _ in BREAKDOWN_SEGMENTS] + ["port_conflicts_per_cycle"]
    return WorkloadResult(
        workload="Average",
        trace_count=sum(r.trace_count for r in results),
        breakdowns={
            cfg: StallBreakdown(**{field: mean(cfg, field) for field in fields})
            for cfg, _, _ in CONFIGS
        },
    )


def display_name(workload: str) -> str:
    return "Average" if workload == "Average" else rename_workload(workload)


def write_summary_csv(path: Path, results: list[WorkloadResult]) -> None:
    fields = ["workload", "display_name", "config", "trace_count"]
    fields += [field for field, _ in BREAKDOWN_SEGMENTS]
    fields += ["total_pct", "port_conflicts_per_cycle"]

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            for cfg, _, _ in CONFIGS:
                b = row.breakdowns[cfg]
                writer.writerow(
                    {
                        "workload": row.workload,
                        "display_name": display_name(row.workload),
                        "config": cfg,
                        "trace_count": row.trace_count,
                        **{
                            field: f"{getattr(b, field):.2f}"
                            for field, _ in BREAKDOWN_SEGMENTS
                        },
                        "total_pct": f"{b.total_pct:.2f}",
                        "port_conflicts_per_cycle": f"{b.port_conflicts_per_cycle:.3f}",
                    }
                )


def write_computation_log(
    path: Path,
    results: list[WorkloadResult],
    config_dirs: dict[str, Path],
    trace_root: Path,
) -> None:
    with path.open("w") as fh:
        fh.write("Backend resource stall breakdown (baseline vs I-Fuse)\n")
        fh.write("=" * 80 + "\n")
        fh.write("All values are simpoint-weighted sums of ROI counters / weighted NODE_CYCLE.\n")
        fh.write("  register file full = MAP_STAGE_STALL_ITSELF\n")
        fh.write(
            "  ROB / LQ / SQ full = MAP_STAGE_STALLED split by FULL_WINDOW_STALL, "
            "LSQ_FULL_LOAD_QUEUE, LSQ_FULL_STORE_QUEUE\n"
        )
        fh.write("  other              = MAP_STAGE_STALLED not covered by those counters\n")
        fh.write("  port conflicts     = RS_OP_READY_NOT_ISSUED_TOTAL / NODE_CYCLE\n")
        fh.write(f"Trace root (weights): {trace_root}\n")
        for cfg, path_ in config_dirs.items():
            fh.write(f"{cfg}: {path_}\n")
        fh.write("\n")
        for row in results:
            fh.write(f"{row.workload} ({display_name(row.workload)})\n")
            fh.write(f"  simpoints: {row.trace_count}\n")
            for cfg, label, _ in CONFIGS:
                b = row.breakdowns[cfg]
                fh.write(
                    f"  {label:9s} total={b.total_pct:6.2f}%  "
                    + "  ".join(
                        f"{BREAKDOWN_CATEGORIES[field]}={getattr(b, field):6.2f}%"
                        for field, _ in BREAKDOWN_SEGMENTS
                    )
                    + f"  ready-not-issued/cycle={b.port_conflicts_per_cycle:.3f}\n"
                )
            fh.write("\n")


def _visible_segments(rows: list[WorkloadResult]) -> list[tuple[str, str]]:
    visible: list[tuple[str, str]] = []
    for field, color in BREAKDOWN_SEGMENTS:
        values = [getattr(r.breakdowns[cfg], field) for r in rows for cfg, _, _ in CONFIGS]
        if max(values) > 0.01:
            visible.append((field, color))
    return visible


def _legend_handles(active_segments: list[tuple[str, str]]) -> list:
    handles = [
        Patch(
            facecolor=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            label=BREAKDOWN_CATEGORIES[field],
        )
        for field, color in active_segments
    ]
    handles += [
        Patch(
            facecolor="white",
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            hatch=hatch,
            label=label,
        )
        for _, label, hatch in CONFIGS
    ]
    return handles


def _style_legend(ax, active_segments: list[tuple[str, str]]) -> None:
    handles = _legend_handles(active_segments)
    legend = ax.legend(
        handles=handles,
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.08),
        bbox_transform=ax.transAxes,
        borderaxespad=0.0,
        fontsize=LEGEND_FONT,
        edgecolor="black",
        labelcolor="black",
        ncol=(len(handles) + 1) // 2,
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
        framealpha=1.0,
    )
    legend.set_clip_on(False)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)


def plot_breakdown(results: list[WorkloadResult], output_dir: Path) -> None:
    rows = results + [average_result(results)]
    labels = [display_name(r.workload) for r in rows]
    x_apps = np.arange(len(results), dtype=float) * APP_STEP
    cluster_half = BAR_OFFSET + BAR_WIDTH / 2.0
    avg_x = float(x_apps[-1] + 2.0 * cluster_half + AVERAGE_GAP)
    x = np.append(x_apps, avg_x)
    separator_x = float(x_apps[-1] + cluster_half + AVERAGE_GAP * 0.5)
    active_segments = _visible_segments(rows)

    _apply_plot_style()
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    offsets = (-BAR_OFFSET, BAR_OFFSET)
    for (cfg, _, hatch), offset in zip(CONFIGS, offsets):
        bottoms = np.zeros(len(rows))
        for field, color in active_segments:
            values = np.array([getattr(r.breakdowns[cfg], field) for r in rows])
            ax.bar(
                x + offset,
                values,
                BAR_WIDTH,
                bottom=bottoms,
                color=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                hatch=hatch,
                zorder=3,
            )
            bottoms += values

    ax.axvline(
        x=separator_x,
        color=AVERAGE_SEPARATOR_COLOR,
        linestyle="--",
        linewidth=AVERAGE_SEPARATOR_WIDTH,
        zorder=2,
    )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", color="black")
    for label in ax.get_xticklabels():
        label.set_fontsize(AXIS_FONT)
        label.set_fontfamily(plot_ipc.FONT_FAMILY)
        if label.get_text() == "Average":
            label.set_weight("bold")

    ax.set_xlim(x[0] - cluster_half - 2.5, x[-1] + cluster_half + 2.5)
    ax.margins(x=0)

    ax.set_ylabel(
        Y_AXIS_LABEL,
        fontsize=AXIS_LABEL_FONT,
        fontfamily=plot_ipc.FONT_FAMILY,
        color="black",
        labelpad=Y_LABEL_PAD,
    )
    ax.set_ylim(0.0, Y_MAX)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=AXIS_FONT, length=0, pad=14, colors="black")
    ax.tick_params(axis="y", labelsize=AXIS_FONT, colors="black")
    for label in ax.get_yticklabels():
        label.set_fontfamily(plot_ipc.FONT_FAMILY)

    plt.subplots_adjust(top=0.62, bottom=0.30, left=0.12, right=0.98)
    _style_legend(ax, active_segments)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(BAR_EDGE_WIDTH)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(
            output_dir / f"{OUTPUT_STEM}.{ext}",
            dpi=OUTPUT_DPI,
            bbox_inches="tight",
            pad_inches=0.08,
        )
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(
        description="Plot backend resource allocation stalls for baseline vs I-Fuse."
    )
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument(
        "--workloads-db",
        type=Path,
        default=None,
        help="use workloads_db.json cluster weights instead of trace-root opt.p/opt.w",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    config_dirs = {
        "baseline": args.baseline_dir or (args.simulations_root / "baseline"),
        "ifuse": args.ifuse_dir or (args.simulations_root / "ifuse"),
    }
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(
        args.trace_root,
        workloads,
        workloads_db=args.workloads_db,
    )

    print("Computing backend resource stall breakdown...")
    for cfg, path in config_dirs.items():
        print(f"  {cfg:8s} {path}")
    print(f"  weights  {args.workloads_db or args.trace_root}")
    print(f"  output   {output_dir}")

    results: list[WorkloadResult] = []
    for workload in workloads:
        row = workload_breakdowns(
            workload,
            sp_weights,
            config_dirs,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if row is None:
            print(f"  skip {workload}: missing baseline/I-Fuse stats")
            continue
        base, ifuse = row.breakdowns["baseline"], row.breakdowns["ifuse"]
        print(
            f"  {workload:14s}  total {base.total_pct:5.1f}% -> {ifuse.total_pct:5.1f}%  "
            f"RF {base.reg_file_pct:5.1f}% -> {ifuse.reg_file_pct:5.1f}%  "
            f"LQ+SQ {base.lq_pct + base.sq_pct:5.1f}% -> {ifuse.lq_pct + ifuse.sq_pct:5.1f}%  "
            f"(simpoints={row.trace_count})"
        )
        results.append(row)

    if not results:
        raise SystemExit("No workloads with complete baseline/I-Fuse stall data.")

    write_summary_csv(output_dir / f"{OUTPUT_STEM}_summary.csv", results + [average_result(results)])
    write_computation_log(
        output_dir / f"{OUTPUT_STEM}_computation_log.txt",
        results + [average_result(results)],
        config_dirs,
        args.workloads_db or args.trace_root,
    )
    plot_breakdown(results, output_dir)

    avg = average_result(results)
    print("\nAverage:")
    for cfg, label, _ in CONFIGS:
        b = avg.breakdowns[cfg]
        print(
            f"  {label:9s} total={b.total_pct:5.2f}%  "
            + "  ".join(
                f"{BREAKDOWN_CATEGORIES[field]}={getattr(b, field):5.2f}%"
                for field, _ in BREAKDOWN_SEGMENTS
            )
        )
    print("\nOutputs:")
    for suffix in (".png", ".pdf", "_summary.csv", "_computation_log.txt"):
        print(f"  - {output_dir / (OUTPUT_STEM + suffix)}")


if __name__ == "__main__":
    main()
