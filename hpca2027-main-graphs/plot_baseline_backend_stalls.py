#!/usr/bin/env python3
"""Baseline backend-bound slots by full structure (PRF, ROB, LQ, SQ).

Reads the Baseline rows of a backend-bound-breakdown_summary.csv written by
plot_backend_bound_breakdown.py, so it works after the raw sims are gone. Each
stack is the top-down Backend Bound, as a percentage of the app's issue slots.

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_baseline_backend_stalls.py \
  --summary /users/deepmish/scarab/src/hpca2027-main-graphs-results/backend-bound-breakdown/backend-bound-breakdown_summary.csv \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/baseline-backend-stalls
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
from plot_ipc import AVERAGE_SEPARATOR_WIDTH, BAR_EDGE_WIDTH, register_noto_serif  # noqa: E402

P = "TOPDOWN_BE_FULL_"
# Paper palette: deep teal, maroon, lime, and light gray, as in Figs. 4 and 6.
RESOURCE = (
    ("PRF", "#0B6162", P + "PRF_SLOTS"),
    ("ROB", "#A81E4C", P + "ROB_SLOTS"),
    ("LQ", "#ACF771", P + "LQ_SLOTS"),
    ("SQ", "#D9D9D9", P + "SQ_SLOTS"),
)

APP_STEP = 10.0
BAR_WIDTH = 6.0
AVERAGE_GAP = 4.0
# Same width as hpca2027-characterization/backend-structure-stalls.pdf (1109 pt), shorter.
FIGSIZE = (1108.6 / 72, 380.0 / 72)
# Lines of the 72 x 22 in IPC-style figures, scaled to FIGSIZE.
SCALE = FIGSIZE[0] / 72.0
AXIS_FONT = 24
LEGEND_FONT = 26
XTICK_FONT = AXIS_FONT
EDGE_WIDTH = BAR_EDGE_WIDTH * SCALE
OUTPUT_STEM = "baseline-backend-stalls"


def load_rows(summary: Path):
    with summary.open() as fh:
        return [(r["workload"], [float(r[k]) for _, _, k in RESOURCE])
                for r in csv.DictReader(fh) if r["config"] == "Baseline"]


def plot(rows, output_dir: Path) -> None:
    x_apps = np.arange(len(rows) - 1, dtype=float) * APP_STEP
    x = np.append(x_apps, x_apps[-1] + APP_STEP + AVERAGE_GAP)
    separator_x = float((x_apps[-1] + x[-1]) / 2)
    stacks = np.array([v for _, v in rows])

    plt.rcParams.update({"font.family": plot_ipc.FONT_FAMILY})
    fig, ax = plt.subplots(figsize=FIGSIZE, layout="constrained")
    # Dotted black gridlines, as in the DBI paper (Seshadri et al., ISCA 2014).
    ax.grid(True, axis="y", linestyle=":", color="black", linewidth=EDGE_WIDTH, zorder=0)
    bottom = np.zeros(len(rows))
    for i, (_, color, _) in enumerate(RESOURCE):
        ax.bar(x, stacks[:, i], BAR_WIDTH, bottom=bottom, color=color,
               edgecolor="black", linewidth=EDGE_WIDTH, zorder=3)
        bottom += stacks[:, i]
    ax.axvline(x=separator_x, color="black", linestyle="--",
               linewidth=3 * AVERAGE_SEPARATOR_WIDTH * SCALE, zorder=2)
    # Headroom for the legend, which sits inside the plot.
    ax.set_ylim(0, 90)
    ax.set_yticks(range(0, 81, 20))
    ax.set_ylabel("Backend stalls (%)", fontsize=AXIS_FONT)
    ax.tick_params(axis="y", labelsize=AXIS_FONT, colors="black",
                   direction="out", length=13, width=EDGE_WIDTH, pad=4)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(EDGE_WIDTH)

    ax.set_xticks(x)
    ax.set_xticklabels([name for name, _ in rows], rotation=45, ha="right",
                       rotation_mode="anchor", color="black")
    ax.tick_params(axis="x", labelsize=XTICK_FONT, colors="black",
                   direction="out", length=13, width=EDGE_WIDTH, pad=4)
    for tick in ax.get_xticklabels():
        if tick.get_text() == "Average":
            tick.set_weight("bold")
    ax.set_xlim(x[0] - APP_STEP / 2, x[-1] + APP_STEP / 2)

    handles = [Patch(facecolor=c, edgecolor="black", linewidth=2 * EDGE_WIDTH, label=name)
               for name, c, _ in RESOURCE]
    legend = ax.legend(handles=handles, loc="upper left", ncol=len(handles),
                       fontsize=LEGEND_FONT, frameon=True, fancybox=False,
                       edgecolor="black", framealpha=1, handlelength=1.0,
                       handleheight=1.0, columnspacing=0.9, handletextpad=0.5, borderaxespad=0.6)
    legend.get_frame().set_linewidth(EDGE_WIDTH)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{OUTPUT_STEM}.{ext}", dpi=300)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows = load_rows(args.summary)
    if not rows:
        raise SystemExit(f"No Baseline rows in {args.summary}.")
    plot(rows, args.output_dir)
    avg = dict(rows)["Average"]
    print("Average", "  ".join(f"{n}={v:.2f}" for (n, _, _), v in zip(RESOURCE, avg)))
    print(f"Outputs in {args.output_dir}")


if __name__ == "__main__":
    main()
