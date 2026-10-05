#!/usr/bin/env python3
"""Plot fused load pairs for ideal fusion vs the I-Fuse candidate funnel for one app.

Reads the committed Helios paper-config results (100M measured instructions after 20M
warmup) and draws, in the style of hpca2027-main-graphs/plot_ipc.py:

  Ideal fusion   IDEAL_FUSION_FUSED_LOADS   retired fused LOAD2s (= fused pairs)
  FCT hits       FCT_LOOKUP_HITS            loads whose PC has an FCT row (LD1 candidates)
  Predicted      IFUSE_LOAD1_PREDICTIONS    FCT hits above the confidence threshold
  Correct        IFUSE_CORRECT_PREDICTIONS  predicted LD2s that matched address and size
  Fused          IFUSE_FUSED_LOADS          retired fused LOAD2s (= fused pairs)

I-Fuse counters come from 1-cycle-delayed I-Fuse. Bars are labeled in millions and as a
percentage of ideal fusion's fused pairs.

Writes <out>/<app>_fusion_candidates.{png,pdf}.

Usage: plot_helios_paper_fusion_candidates.py [--app leela_s] [--scarab ~/scarab] [--out <dir>]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))
import plot_ipc  # noqa: E402

RESULTS = "src/hpca2027-revision"
IDEAL = ("origin/hpca2027-revision-ideal-fusion", f"{RESULTS}/helios-paper-config-ideal-fusion")
IFUSE = ("a0565667f", f"{RESULTS}/helios-paper-config-ifuse")
# label, counter, which run, color
BARS = (
    ("Ideal fusion\nfused pairs", "IDEAL_FUSION_FUSED_LOADS", IDEAL, plot_ipc.IDEAL_FUSION_COLOR),
    ("I-Fuse\nFCT hits", "FCT_LOOKUP_HITS", IFUSE, plot_ipc.IFUSE_1CYCLE_COLOR),
    ("I-Fuse\npredicted", "IFUSE_LOAD1_PREDICTIONS", IFUSE, plot_ipc.IFUSE_1CYCLE_COLOR),
    ("I-Fuse\ncorrect", "IFUSE_CORRECT_PREDICTIONS", IFUSE, plot_ipc.IFUSE_1CYCLE_COLOR),
    ("I-Fuse\nfused pairs", "IFUSE_FUSED_LOADS", IFUSE, plot_ipc.IFUSE_1CYCLE_COLOR),
)
FIGSIZE = (40.0, 23.0)
BAR_WIDTH = 0.6


def read_count(scarab: Path, run: tuple[str, str], app: str, counter: str) -> int:
    """Post-warmup count (<stat>_count) of counter from any *.stat.0.csv of the run."""
    ref, root = run
    files = subprocess.run(["git", "-C", str(scarab), "ls-tree", "--name-only", f"{ref}:{root}/{app}"],
                           check=True, capture_output=True, text=True).stdout.split()
    for name in (f for f in files if f.endswith(".stat.0.csv")):
        text = subprocess.run(["git", "-C", str(scarab), "show", f"{ref}:{root}/{app}/{name}"],
                              check=True, capture_output=True, text=True).stdout
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[0] == f"{counter}_count":
                return int(parts[2])
    raise KeyError(f"{counter} not found for {app} at {ref}:{root}")


def plot(app: str, values: list[int], out: Path) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    p = plot_ipc
    p._apply_ipc_plot_style()
    fig, ax = plt.subplots(figsize=FIGSIZE)
    xs = list(range(len(BARS)))
    millions = [v / 1e6 for v in values]
    ax.bar(xs, millions, BAR_WIDTH, color=[color for *_, color in BARS], edgecolor="black",
           linewidth=p.BAR_EDGE_WIDTH, zorder=3)
    top = max(millions)
    for x, m, v in zip(xs, millions, values):
        ax.text(x, m + 0.02 * top, f"{m:.2f}M\n({100 * v / values[0]:.0f}%)", ha="center", va="bottom",
                fontsize=p.IPC_AXIS_FONT, fontfamily=p.FONT_FAMILY, color="black", zorder=4)

    ax.set_xticks(xs)
    ax.set_xticklabels([label for label, *_ in BARS], fontsize=p.IPC_AXIS_FONT,
                       fontfamily=p.FONT_FAMILY, color="black")
    ax.get_xticklabels()[0].set_fontweight("bold")
    ax.tick_params(axis="x", length=0, pad=14, colors="black")
    ax.tick_params(axis="y", labelsize=p.IPC_TICK_FONT, colors="black")
    ax.set_xlim(-0.6, len(BARS) - 0.4)
    ax.axvline(x=0.5, color=p.AVERAGE_SEPARATOR_COLOR, linestyle="--",
               linewidth=p.AVERAGE_SEPARATOR_WIDTH, zorder=2)

    ax.set_ylabel("Count (millions)", fontsize=p.IPC_AXIS_LABEL_FONT, fontfamily=p.FONT_FAMILY,
                  color="black")
    step = 5.0 if top > 10 else 2.0
    ax.set_ylim(0.0, (int(top * 1.3 / step) + 1) * step)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(step))
    ax.yaxis.set_minor_locator(mticker.MultipleLocator(step / 2))
    ax.tick_params(axis="y", which="minor", length=0)
    p._apply_speedup_y_grid(ax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    for label in ax.get_yticklabels():
        label.set_fontfamily(p.FONT_FAMILY)
    ax.set_title(app.removesuffix("_s"), fontsize=p.IPC_AXIS_LABEL_FONT, fontfamily=p.FONT_FAMILY,
                 color="black", pad=30)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(p.BAR_EDGE_WIDTH)

    for ext in ("png", "pdf"):
        fig.savefig(f"{out}.{ext}", bbox_inches="tight", pad_inches=0.15, dpi=300)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="leela_s")
    ap.add_argument("--scarab", type=Path, default=Path.home() / "scarab")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    out_dir = args.out or args.scarab / RESULTS / "helios-paper-config-ifuse"

    values = [read_count(args.scarab, run, args.app, counter) for _, counter, run, _ in BARS]
    for (label, counter, *_), v in zip(BARS, values):
        print(f"{counter:28s} {v:>12,d}  {100 * v / values[0]:5.1f}% of ideal")

    plot_ipc.register_noto_serif()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.app}_fusion_candidates"
    plot(args.app, values, out)
    print(f"Wrote {out}.png and {out}.pdf")


if __name__ == "__main__":
    main()
