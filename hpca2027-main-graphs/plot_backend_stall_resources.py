#!/usr/bin/env python3
"""Backend-bound slots by full structure (PRF, ROB, IQ, LQ, SQ), baseline vs I-Fuse.

Each backend-bound slot is charged to the structure that stopped allocation
(topdown_backend_attribute() in topdown.c), so each stack is Backend Bound.
Scarab never stalls dispatch on a full IQ, so IQ comes from the ROB head state
(head not yet given an IQ entry); structures that never stall are hidden. Bars
are a percentage of the baseline's issue slots per app. The summary CSV also
lists what the ROB head waited on (memory vs non-memory).

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_backend_stall_resources.py \
  --baseline-dir /users/deepmish/scarab/src/simulations/rb-baseline \
  --ifuse-dir /users/deepmish/scarab/src/hpca2027-revision-main-results/datacenter/ifuse \
  --trace-root /dev/shm/ifuse \
  --output-dir /users/deepmish/scarab/src/hpca2027-revision-main-results/backend-stall-resources
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
from plot_backend_bound_breakdown import COUNTERS, workload_pcts  # noqa: E402
from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    DEFAULT_TRACE_ROOT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    load_simpoint_trace_weights,
    register_noto_serif,
    rename_workload,
)

P = "TOPDOWN_BE_"
MEMORY_HEAD = ("LLC_MISS", "L1D_MISS", "L1D_HIT", "DCACHE_PORT", "STORE", "FUSED_LD2")
# Colors of the Helios paper (MICRO 2022) figures, ColorBrewer Spectral.
RESOURCE = (
    ("PRF", "#5E4FA1", (P + "FULL_PRF_SLOTS",)),
    ("ROB", "#328795", (P + "FULL_ROB_SLOTS",)),
    ("IQ", "#66C1A4", (P + "HEAD_IQ_SLOTS",)),
    ("LQ", "#8EE08A", (P + "FULL_LQ_SLOTS",)),
    ("SQ", "#E5F497", (P + "FULL_SQ_SLOTS",)),
)
CAUSE = (
    ("Memory: LLC miss", "#4A3AA7", (P + "HEAD_LLC_MISS_SLOTS",)),
    ("Memory: L1-D miss", "#2A78D6", (P + "HEAD_L1D_MISS_SLOTS",)),
    ("Memory: L1-D hit, port, store", "#7FB2EA",
     tuple(f"{P}HEAD_{h}_SLOTS" for h in ("L1D_HIT", "DCACHE_PORT", "STORE", "FUSED_LD2"))),
    ("Non-memory", "#C8C8C6",
     tuple(f"{P}HEAD_{h}_SLOTS" for h in ("IQ", "PORT", "EXEC", "DEP", "DONE", "EMPTY"))),
)
CONFIGS = ("Baseline", "I-Fuse")
HATCHES = ("", "/")

APP_STEP = 10.0
BAR_WIDTH = 3.6
BAR_GAP = 0.0
AVERAGE_GAP = 2.4
FIGSIZE = (72.0, 22.0)
AXIS_FONT = IPC_TICK_FONT
TITLE_FONT = 90
LEGEND_FONT = 90
OUTPUT_STEM = "backend-stall-resources"


def group(row, categories):
    return [sum(row[k] for k in keys) for _, _, keys in categories]


def hatch_color(color):
    """White hatch on dark fills, near-black on light ones."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#FFFFFF" if 0.299 * r + 0.587 * g + 0.114 * b < 140 else "#333333"


def draw_panel(ax, rows, categories, title, x, offsets, separator_x):
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    peak = 0.0
    for label, offset, hatch in zip(CONFIGS, offsets, HATCHES):
        stacks = np.array([group(r[label], categories) for _, r in rows])
        bottom = np.zeros(len(rows))
        for i, (_, color, _) in enumerate(categories):
            # Matplotlib draws hatches in the edge color, so draw the hatched fill
            # first and the black outline on top of it.
            ax.bar(x + offset, stacks[:, i], BAR_WIDTH, bottom=bottom, color=color, hatch=hatch,
                   edgecolor=hatch_color(color), linewidth=0, zorder=3)
            ax.bar(x + offset, stacks[:, i], BAR_WIDTH, bottom=bottom, fill=False,
                   edgecolor="black", linewidth=BAR_EDGE_WIDTH, zorder=4)
            bottom += stacks[:, i]
        peak = max(peak, bottom.max())
    ax.set_ylim(0, peak * 1.08)
    ax.axvline(x=separator_x, color="black", linestyle="--",
               linewidth=3 * AVERAGE_SEPARATOR_WIDTH, zorder=2)
    ax.set_title(title, fontsize=TITLE_FONT, loc="left", pad=16)
    ax.set_ylabel("Processor stalls (%)", fontsize=AXIS_FONT)
    ax.tick_params(axis="y", labelsize=AXIS_FONT, colors="black")
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(BAR_EDGE_WIDTH)


def plot(rows, output_dir: Path) -> None:
    n = len(CONFIGS)
    cluster = n * BAR_WIDTH + (n - 1) * BAR_GAP
    offsets = [-cluster / 2 + BAR_WIDTH / 2 + i * (BAR_WIDTH + BAR_GAP) for i in range(n)]
    x_apps = np.arange(len(rows) - 1, dtype=float) * APP_STEP
    x = np.append(x_apps, x_apps[-1] + cluster + AVERAGE_GAP)
    separator_x = float(x_apps[-1] + cluster / 2 + AVERAGE_GAP * 0.5)

    plt.rcParams.update({"font.family": plot_ipc.FONT_FAMILY, "hatch.linewidth": 2.0})
    fig, bottom = plt.subplots(figsize=FIGSIZE)
    # Hide structures that never stall (e.g. IQ: Scarab never blocks dispatch on it).
    shown = tuple(c for c in RESOURCE
                  if any(group(r[label], (c,))[0] > 0.05 for _, r in rows for label in CONFIGS))
    draw_panel(bottom, rows, shown, "", x, offsets, separator_x)

    bottom.set_xticks(x)
    bottom.set_xticklabels([name for name, _ in rows], rotation=45, ha="right", color="black")
    bottom.tick_params(axis="x", labelsize=AXIS_FONT, length=0, pad=14)
    for tick in bottom.get_xticklabels():
        if tick.get_text() == "Average":
            tick.set_weight("bold")
    bottom.set_xlim(x[0] - cluster / 2 - 2.0, x[-1] + cluster / 2 + 2.0)

    handles = [Patch(facecolor="#BBBBBB", hatch=h, edgecolor="black", linewidth=BAR_EDGE_WIDTH,
                     label=label) for label, h in zip(CONFIGS, HATCHES)]
    handles += [Patch(facecolor=c, edgecolor="black", linewidth=BAR_EDGE_WIDTH, label=name)
                for name, c, _ in shown]
    legend = fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.86),
                        ncol=len(handles), fontsize=LEGEND_FONT, frameon=True, fancybox=False,
                        edgecolor="black", handlelength=1.6, columnspacing=1.4)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    plt.subplots_adjust(top=0.85)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{OUTPUT_STEM}.{ext}", dpi=300, bbox_inches="tight",
                    pad_inches=0.08)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--ifuse-dir", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    configs = list(zip(CONFIGS, (args.baseline_dir, args.ifuse_dir)))
    sp_weights = load_simpoint_trace_weights(args.trace_root, SIMPOINT_WORKLOADS)
    rows = []
    for workload in SIMPOINT_WORKLOADS:
        result = workload_pcts(workload, sp_weights, configs)
        if result is None:
            print(f"  skip {workload}: no simpoint with stats in both configs")
            continue
        rows.append((rename_workload(workload), result))
    if not rows:
        raise SystemExit("No workloads with stats in both configs.")
    rows.append(("Average", {label: {k: sum(r[label][k] for _, r in rows) / len(rows)
                                     for k in COUNTERS} for label in CONFIGS}))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    names = [n for n, _, _ in RESOURCE + CAUSE]
    with (args.output_dir / f"{OUTPUT_STEM}_summary.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["workload", "config", *names])
        for name, r in rows:
            for label in CONFIGS:
                vals = group(r[label], RESOURCE) + group(r[label], CAUSE)
                writer.writerow([name, label, *(f"{v:.2f}" for v in vals)])

    plot(rows, args.output_dir)
    for label in CONFIGS:
        avg = rows[-1][1][label]
        print(label, "  ".join(f"{n}={v:.1f}" for n, v in
                               zip(names, group(avg, RESOURCE) + group(avg, CAUSE))))
    print(f"Outputs in {args.output_dir}")


if __name__ == "__main__":
    main()
