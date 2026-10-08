#!/usr/bin/env python3
"""Speedup of Helios, RFP, and ideal fusion over no fusion (paper Fig. 3), from a CSV.

The CSV has columns workload,helios,rfp,ideal_fusion (speedup in %), with an
Average row last; lines starting with # are skipped. The layout matches the
figure plot_helios_ideal_ipc.py made for the submission, with outward
DBI-style ticks on both axes.

Example:
  /users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
    /users/deepmish/scarab-infra/hpca2027-characterization/plot_ipc_helios_rfp_ideal_csv.py \\
    --csv /users/deepmish/scarab-infra/hpca2027-characterization/ipc_helios_rfp_ideal.csv \\
    --output-dir /users/deepmish/hpca2027-paper/Results/Characterization
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

MAIN_GRAPHS = Path(__file__).resolve().parents[1] / "hpca2027-main-graphs"
if str(MAIN_GRAPHS) not in sys.path:
    sys.path.insert(0, str(MAIN_GRAPHS))

from plot_ipc import (  # noqa: E402
    FONT_FAMILY,
    HELIOS_COLOR,
    IDEAL_FUSION_COLOR,
    RFP_COLOR,
    register_noto_serif,
)

SERIES = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ideal_fusion", "Ideal fusion", IDEAL_FUSION_COLOR),
)

# Geometry in points, from the submitted figure.
FIG_W, FIG_H = 2904.0, 1167.0
AX_LEFT, AX_RIGHT, AX_TOP, AX_BOTTOM = 416.4, 2893.2, 216.8, 700.6
APP_STEP = 1.0
BAR_WIDTH = 0.233
AVERAGE_STEP = 0.956
X_PAD = 0.472
TICK_FONT = 72
LEGEND_FONT = 63
LINE_WIDTH = 2.5
SEPARATOR_COLOR = "#4A4A4A"
# Outward tick dashes, as in the DBI paper, at the proportions of Fig. 4.
TICK_LENGTH = 36
TICK_PAD = 8
Y_LABEL = "Speedup (%)\n(normalized to\nno-fusion)"
OUTPUT_STEM = "ipc-helios-rfp-ideal"


def load_rows(path: Path):
    with path.open() as fh:
        lines = [line for line in fh if not line.startswith("#")]
    return [(r["workload"], [float(r[k]) for k, _, _ in SERIES]) for r in csv.DictReader(lines)]


def plot(rows, output_dir: Path) -> None:
    x_apps = np.arange(len(rows) - 1, dtype=float) * APP_STEP
    x = np.append(x_apps, x_apps[-1] + AVERAGE_STEP)
    separator_x = float((x_apps[-1] + x[-1]) / 2)
    values = np.array([v for _, v in rows])

    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
        "text.color": "black",
        "axes.labelcolor": "black",
    })
    fig = plt.figure(figsize=(FIG_W / 72, FIG_H / 72))
    ax = fig.add_axes((AX_LEFT / FIG_W, 1 - AX_BOTTOM / FIG_H,
                       (AX_RIGHT - AX_LEFT) / FIG_W, (AX_BOTTOM - AX_TOP) / FIG_H))
    ax.set_axisbelow(True)
    ax.yaxis.set_minor_locator(mticker.MultipleLocator(5))
    ax.grid(True, axis="y", which="both", linestyle=(0, (1.0, 1.65)), color="black",
            linewidth=2.0)
    for i, (_, label, color) in enumerate(SERIES):
        ax.bar(x + (i - 1) * BAR_WIDTH, values[:, i], BAR_WIDTH, color=color,
               edgecolor="black", linewidth=LINE_WIDTH, label=label, zorder=3)
    ax.axvline(x=separator_x, color=SEPARATOR_COLOR, linestyle=(0, (3.7, 1.6)),
               linewidth=5.0, zorder=2)

    ax.set_xticks(x)
    ax.set_xticklabels([name for name, _ in rows], rotation=45, ha="right",
                       rotation_mode="anchor", fontsize=TICK_FONT, color="black")
    for label in ax.get_xticklabels():
        if label.get_text() == "Average":
            label.set_weight("bold")
    ax.set_xlim(x[0] - X_PAD, x[-1] + X_PAD)

    ax.set_ylim(0, 50)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.set_ylabel(Y_LABEL, fontsize=TICK_FONT)
    for axis in ("x", "y"):
        ax.tick_params(axis=axis, which="major", labelsize=TICK_FONT, colors="black",
                       direction="out", length=TICK_LENGTH, width=LINE_WIDTH, pad=TICK_PAD)
    ax.tick_params(axis="y", which="minor", length=0)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(LINE_WIDTH)

    handles = [Patch(facecolor=c, edgecolor="black", linewidth=LINE_WIDTH, label=l)
               for _, l, c in SERIES]
    legend = ax.legend(handles=handles, loc="lower center",
                       bbox_to_anchor=(0.5, 1 + 51 / (AX_BOTTOM - AX_TOP)), ncol=len(handles),
                       fontsize=LEGEND_FONT, frameon=True, fancybox=False, edgecolor="black",
                       framealpha=1.0, borderaxespad=0.0, handlelength=0.95, handleheight=0.86)
    legend.get_frame().set_linewidth(LINE_WIDTH)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{OUTPUT_STEM}.{ext}", dpi=100,
                    bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = load_rows(args.csv)
    plot(rows, args.output_dir)
    print("Average", rows[-1][1])


if __name__ == "__main__":
    main()
