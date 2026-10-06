#!/usr/bin/env python3
"""Backend Bound broken down by backend resource, for baseline and I-Fuse.

Every backend-bound issue slot is charged to exactly one cause in scarab's
topdown_backend_attribute() (topdown.c), so each panel's stack is the
top-down Backend Bound itself, split two ways:

  (a) Full structure: what stopped allocation in that cycle.
        PRF (rename: no free physical register), ROB, LQ, SQ (dispatch), or
        Other (a partly issued group with nothing full).

  (b) ROB head: why the window was not draining.
        LLC miss, L1-D miss (hit in L2/LLC), L1-D hit latency, L1-D port or MSHR
        conflict, store, fused LOAD2 waiting on LOAD1, scheduler/IQ full (head has no IQ
        entry yet), execution ports (head ready but not picked), execution
        latency (head issued, still executing), dependencies (head waiting on
        an operand), retire/other (head done, or ROB empty).

All bars are a percentage of the baseline's issue slots for that app, so a
shorter I-Fuse segment is an absolute reduction of that stall.

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_backend_bound_breakdown.py \
  --config "Baseline=/users/deepmish/scarab/src/simulations/rb-baseline" \
  --config "I-Fuse=/users/deepmish/scarab/src/simulations/rb-ifuse" \
  --config "I-Fuse (1-cycle delay)=/users/deepmish/scarab/src/simulations/rb-ifuse1c" \
  --trace-root /dev/shm/ifuse \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/backend-bound-breakdown
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

import plot_ipc  # noqa: E402
from plot_fusion_fraction import stat_count_from_csv  # noqa: E402
from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    register_noto_serif,
    rename_workload,
)

CORE = "core.stat.0.csv"
FULL = (
    ("TOPDOWN_BE_FULL_PRF_SLOTS", "PRF", "#1BAF7A"),
    ("TOPDOWN_BE_FULL_ROB_SLOTS", "ROB", "#2A78D6"),
    ("TOPDOWN_BE_FULL_LQ_SLOTS", "Load queue", "#E87BA4"),
    ("TOPDOWN_BE_FULL_SQ_SLOTS", "Store queue", "#EB6834"),
    ("TOPDOWN_BE_FULL_OTHER_SLOTS", "Other", "#C8C8C6"),
)
HEAD = (
    ("TOPDOWN_BE_HEAD_LLC_MISS_SLOTS", "Memory: LLC miss", "#4A3AA7"),
    ("TOPDOWN_BE_HEAD_L1D_MISS_SLOTS", "Memory: L1-D miss (L2/LLC hit)", "#2A78D6"),
    ("TOPDOWN_BE_HEAD_L1D_HIT_SLOTS", "Memory: L1-D hit latency", "#1BAF7A"),
    ("TOPDOWN_BE_HEAD_DCACHE_PORT_SLOTS", "Memory: L1-D port / MSHR", "#EDA100"),
    ("TOPDOWN_BE_HEAD_STORE_SLOTS", "Memory: store", "#EB6834"),
    ("TOPDOWN_BE_HEAD_FUSED_LD2_SLOTS", "Memory: fused LOAD2 wait", "#E87BA4"),
    ("TOPDOWN_BE_HEAD_IQ_SLOTS", "Scheduler (IQ) full", "#E34948"),
    ("TOPDOWN_BE_HEAD_PORT_SLOTS", "Execution ports", "#8C564B"),
    ("TOPDOWN_BE_HEAD_EXEC_SLOTS", "Execution latency", "#008300"),
    ("TOPDOWN_BE_HEAD_DEP_SLOTS", "Dependencies", "#7F7F00"),
    ("TOPDOWN_BE_HEAD_DONE_SLOTS", "Retire / other", "#C8C8C6"),
    ("TOPDOWN_BE_HEAD_EMPTY_SLOTS", None, "#C8C8C6"),  # folded into Retire / other
)
TOTAL_KEY = "TOPDOWN_TOTAL_SLOTS"
COUNTERS = [TOTAL_KEY] + [k for k, _, _ in FULL + HEAD]
Y_LABEL = "Backend-bound slots\n(% of baseline issue slots)"
HATCHES = ("", "//", "xx")

APP_STEP = 10.0
BAR_WIDTH = 2.6
BAR_GAP = 0.25
AVERAGE_GAP = 2.4
FIGSIZE = (72.0, 23.0)
AXIS_FONT = IPC_TICK_FONT
TITLE_FONT = 100
LEGEND_FONT = 72
OUTPUT_DPI = 300
OUTPUT_STEM = "backend-bound-breakdown"
FULL_STEM = "backend-bound-full-structure"
HEAD_STEM = "backend-bound-rob-head"


def simpoint_counts(sim_dir: Path) -> dict[str, float] | None:
    counts = {}
    for stat in COUNTERS:
        value = stat_count_from_csv(sim_dir / CORE, f"{stat}_count")
        if value is None:
            return None
        counts[stat] = value
    return counts


def workload_pcts(workload, sp_weights, configs):
    """Simpoint-weighted slots per config, as % of the first (baseline) config's slots."""
    totals = {label: {k: 0.0 for k in COUNTERS} for label, _ in configs}
    used = 0
    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue
        per_cfg = {}
        for label, path in configs:
            sim_dir = find_simpoint_dir(path, path.name, workload, cluster_id,
                                        suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE)
            per_cfg[label] = simpoint_counts(sim_dir) if sim_dir else None
        if any(v is None for v in per_cfg.values()):
            continue
        for label, counts in per_cfg.items():
            for k, v in counts.items():
                totals[label][k] += weight * v
        used += 1
    if not used:
        return None
    base_slots = totals[configs[0][0]][TOTAL_KEY]
    return {label: {k: 100.0 * t[k] / base_slots for k in COUNTERS} for label, t in totals.items()}


def stack_values(row, categories):
    """Category name -> value, folding unnamed categories into the one before."""
    out, last = [], None
    for key, name, color in categories:
        if name is None:
            out[-1] = (out[-1][0], out[-1][1], out[-1][2] + row[key])
        else:
            out.append((name, color, row[key]))
        last = name
    return out


def draw_panel(ax, rows, configs, categories, title, x, offsets, separator_x):
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    ref = stack_values(rows[0][1][configs[0][0]], categories)
    # Drop categories that are zero in every app and config; they only clutter the legend.
    shown = [i for i in range(len(ref))
             if any(stack_values(r[label], categories)[i][2] > 0.05
                    for _, r in rows for label, _ in configs)]
    peak = 0.0
    for (label, _), offset, hatch in zip(configs, offsets, HATCHES):
        bottom = np.zeros(len(rows))
        for i in shown:
            values = np.array([stack_values(r[label], categories)[i][2] for _, r in rows])
            ax.bar(x + offset, values, BAR_WIDTH, bottom=bottom, color=ref[i][1], hatch=hatch,
                   edgecolor="black", linewidth=BAR_EDGE_WIDTH, zorder=3)
            bottom += values
        peak = max(peak, bottom.max())
    ax.set_ylim(0, peak * 1.08)
    ax.axvline(x=separator_x, color=AVERAGE_SEPARATOR_COLOR, linestyle="--",
               linewidth=AVERAGE_SEPARATOR_WIDTH, zorder=2)
    ax.set_title(title, fontsize=TITLE_FONT, loc="left", pad=16)
    ax.tick_params(axis="y", labelsize=AXIS_FONT, colors="black")
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(BAR_EDGE_WIDTH)
    handles = [Patch(facecolor=ref[i][1], edgecolor="black", linewidth=BAR_EDGE_WIDTH,
                     label=ref[i][0]) for i in reversed(shown)]
    legend = ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.005, 1.0),
                       fontsize=LEGEND_FONT, frameon=True, fancybox=False, edgecolor="black")
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)


def plot(rows, configs, output_dir: Path, categories, title, stem) -> None:
    n = len(configs)
    cluster = n * BAR_WIDTH + (n - 1) * BAR_GAP
    offsets = [-cluster / 2 + BAR_WIDTH / 2 + i * (BAR_WIDTH + BAR_GAP) for i in range(n)]
    n_apps = len(rows) - 1
    x_apps = np.arange(n_apps, dtype=float) * APP_STEP
    avg_x = float(x_apps[-1] + cluster + AVERAGE_GAP)
    x = np.append(x_apps, avg_x)
    separator_x = float(x_apps[-1] + cluster / 2 + AVERAGE_GAP * 0.5)

    plt.rcParams.update({"font.family": plot_ipc.FONT_FAMILY, "hatch.linewidth": 3.0})
    fig, ax = plt.subplots(figsize=FIGSIZE)
    draw_panel(ax, rows, configs, categories, title, x, offsets, separator_x)
    ax.set_ylabel(Y_LABEL, fontsize=AXIS_FONT)

    ax.set_xticks(x)
    ax.set_xticklabels([name for name, _ in rows], rotation=45, ha="right", color="black")
    ax.tick_params(axis="x", labelsize=AXIS_FONT, length=0, pad=14)
    for tick in ax.get_xticklabels():
        if tick.get_text() == "Average":
            tick.set_weight("bold")
    ax.set_xlim(x[0] - cluster / 2 - 2.0, x[-1] + cluster / 2 + 2.0)

    config_handles = [Patch(facecolor="white", hatch=h, edgecolor="black",
                            linewidth=BAR_EDGE_WIDTH, label=label)
                      for (label, _), h in zip(configs, HATCHES)]
    ax.get_legend().remove()
    legend = fig.legend(handles=config_handles, loc="upper center", bbox_to_anchor=(0.48, 1.0),
                        ncol=n, fontsize=TITLE_FONT, frameon=True, fancybox=False,
                        edgecolor="black", handlelength=1.6, columnspacing=1.4)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    # draw_panel's category legend was replaced above; draw it again beside the axes.
    ref = stack_values(rows[0][1][configs[0][0]], categories)
    shown = [i for i in range(len(ref))
             if any(stack_values(r[label], categories)[i][2] > 0.05
                    for _, r in rows for label, _ in configs)]
    cat = ax.figure.legend(handles=[Patch(facecolor=ref[i][1], edgecolor="black",
                                          linewidth=BAR_EDGE_WIDTH, label=ref[i][0])
                                    for i in reversed(shown)],
                           loc="center left", bbox_to_anchor=(0.905, 0.5), fontsize=LEGEND_FONT,
                           frameon=True, fancybox=False, edgecolor="black")
    cat.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    plt.subplots_adjust(top=0.72, bottom=0.2, left=0.08, right=0.9)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{stem}.{ext}", dpi=OUTPUT_DPI, bbox_inches="tight",
                    pad_inches=0.08)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", action="append", required=True,
                        help='"Label=/path/to/simulations/<exp>"; the first is the baseline')
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--unit", choices=("slots", "cycles"), default="slots",
                        help="slots: backend-bound issue slots; cycles: fully stalled cycles")
    args = parser.parse_args()

    global FULL, HEAD, TOTAL_KEY, COUNTERS, Y_LABEL, OUTPUT_STEM, FULL_STEM, HEAD_STEM
    if args.unit == "cycles":
        to_cycles = lambda cats: tuple((k.replace("_SLOTS", "_CYCLES"), n, c) for k, n, c in cats)
        FULL, HEAD = to_cycles(FULL), to_cycles(HEAD)
        TOTAL_KEY = "NODE_CYCLE"
        COUNTERS = [TOTAL_KEY] + [k for k, _, _ in FULL + HEAD]
        Y_LABEL = "Stalled cycles\n(% of baseline cycles)"
        OUTPUT_STEM, FULL_STEM, HEAD_STEM = (f"{stem}-cycles" for stem in
                                             (OUTPUT_STEM, FULL_STEM, HEAD_STEM))

    configs = [(label, Path(path)) for label, _, path in
               (spec.partition("=") for spec in args.config)]
    if not 1 <= len(configs) <= len(HATCHES):
        raise SystemExit(f"Pass 1 to {len(HATCHES)} --config values.")

    sp_weights = load_simpoint_trace_weights(args.trace_root, SIMPOINT_WORKLOADS)
    rows = []
    for workload in SIMPOINT_WORKLOADS:
        result = workload_pcts(workload, sp_weights, configs)
        if result is None:
            print(f"  skip {workload}: no simpoint with stats in every config")
            continue
        rows.append((rename_workload(workload), result))
    if not rows:
        raise SystemExit("No workloads with stats in every config.")

    avg = {label: {k: sum(r[label][k] for _, r in rows) / len(rows) for k in COUNTERS}
           for label, _ in configs}
    rows.append(("Average", avg))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / f"{OUTPUT_STEM}_summary.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["workload", "config", *COUNTERS[1:]])
        for name, r in rows:
            for label, _ in configs:
                writer.writerow([name, label, *(f"{r[label][k]:.2f}" for k in COUNTERS[1:])])

    plot(rows, configs, args.output_dir, FULL, "Full structure that stalled rename (PRF) or dispatch (ROB, LQ, SQ)", FULL_STEM)
    plot(rows, configs, args.output_dir, HEAD, "What the ROB head was waiting on", HEAD_STEM)
    for label, _ in configs:
        full = "  ".join(f"{n}={avg[label][k]:.1f}" for k, n, _ in FULL)
        head = "  ".join(f"{n}={avg[label][k]:.1f}" for k, n, _ in HEAD if n)
        print(f"{label}\n  full: {full}\n  head: {head}")
    print(f"Outputs in {args.output_dir}")


if __name__ == "__main__":
    main()
