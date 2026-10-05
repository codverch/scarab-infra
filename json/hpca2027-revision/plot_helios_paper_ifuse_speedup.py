#!/usr/bin/env python3
"""Plot I-Fuse speedup over no fusion on the Helios paper config.

Reads summary.csv files committed in the scarab repo (100M measured instructions after
20M warmup, PARAMS.helios_paper) and draws them in the style of
hpca2027-main-graphs/plot_ipc.py's speedup bars: 1-cycle-delayed I-Fuse next to ideal
fusion, each normalized to the no-fusion baseline. Average is the arithmetic mean of the
per-app speedups, as in plot_ipc.py.

Writes <out>/speedup.{png,pdf} and speedup-labeled.{png,pdf}.

Usage: plot_helios_paper_ifuse_speedup.py [--scarab ~/scarab] [--out <dir>]
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))
import plot_ipc  # noqa: E402

RESULTS = "src/hpca2027-revision"
BASELINE = ("origin/hpca2027-revision-baseline", f"{RESULTS}/helios-paper-config-baseline/summary.csv")
# key, legend label, color, scarab ref, summary.csv path, IPC column
SERIES = (
    ("ifuse_1cycle", "1-cycle-delayed I-Fuse", plot_ipc.IFUSE_1CYCLE_COLOR,
     "a0565667f", f"{RESULTS}/helios-paper-config-ifuse/summary.csv", "ipc"),
    ("ideal_fusion", "Ideal fusion", plot_ipc.IDEAL_FUSION_COLOR,
     "origin/hpca2027-revision-ideal-fusion", f"{RESULTS}/helios-paper-config-ideal-fusion/summary.csv",
     "ipc_ideal_fusion"),
)
APP_LABELS = {
    "deepsjeng_s": "deepsjeng",
    "exchange2_s": "exchange2",
    "gcc_s": "gcc-1",
    "gcc_s_2": "gcc-2",
    "gcc_s_3": "gcc-3",
    "leela_s": "leela",
    "mcf_s": "mcf",
    "omnetpp_s": "omnetpp",
    "xalancbmk_s": "xalancbmk",
}
BAR_WIDTH = 2.6
# Speedups here are a few percent, so ticks every 1% (2% past 5%) instead of plot_ipc's 10%.


def read_ipc(scarab: Path, ref: str, path: str, column: str = "ipc") -> dict[str, float]:
    text = subprocess.run(["git", "-C", str(scarab), "show", f"{ref}:{path}"],
                          check=True, capture_output=True, text=True).stdout
    return {r["app"]: float(r[column]) for r in csv.DictReader(io.StringIO(text))}


def plot(workloads: list[str], pct: list[list[float]], out_dir: Path) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    p = plot_ipc
    series_all = [vals + [sum(vals) / len(vals)] for vals in pct]
    x_apps = [i * p.APP_STEP for i in range(len(workloads))]
    avg_x = x_apps[-1] + BAR_WIDTH + p.AVERAGE_GAP + BAR_WIDTH
    separator_x = x_apps[-1] + BAR_WIDTH + p.AVERAGE_GAP * p.AVERAGE_SEPARATOR_FRAC
    x_ticks = x_apps + [avg_x]
    labels = [APP_LABELS.get(w, w) for w in workloads] + ["Average"]
    top = max(max(v) for v in series_all)
    handles = tuple((key, label, color) for key, label, color, *_ in SERIES)

    for stem, labeled in (("speedup-labeled", True), ("speedup", False)):
        p._apply_ipc_plot_style()
        fig, ax = plt.subplots(figsize=p.IPC_FIGSIZE)
        ymax_pct = top * (1.25 if labeled else 1.0)
        step = 1.0 if ymax_pct <= 5.0 else 2.0
        ymax = int(ymax_pct / step) + 1
        # As in plot_ipc.py, negative speedups draw at zero and keep their label.
        for lane, ((_, _, color, *_), vals, off) in enumerate(
                zip(SERIES, series_all, (-BAR_WIDTH / 2, BAR_WIDTH / 2))):
            bars = ax.bar([x + off for x in x_ticks], [max(0.0, v) for v in vals], BAR_WIDTH,
                          color=color, edgecolor="black", linewidth=p.BAR_EDGE_WIDTH, zorder=3)
            if labeled:
                for x, v, bar in zip(x_ticks, vals, bars):
                    ax.text(x + off, max(0.0, v) + 0.03 * ymax * step, f"{v:+.1f}", ha="center", va="bottom",
                            rotation=90, fontsize=p.IPC_AXIS_FONT, fontfamily=p.FONT_FAMILY,
                            color="black", zorder=4)

        ax.axvline(x=separator_x, color=p.AVERAGE_SEPARATOR_COLOR, linestyle="--",
                   linewidth=p.AVERAGE_SEPARATOR_WIDTH, zorder=2)
        ax.set_xticks(x_ticks)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=p.IPC_TICK_FONT,
                           fontfamily=p.FONT_FAMILY, color="black")
        ax.get_xticklabels()[-1].set_fontweight("bold")
        ax.tick_params(axis="x", labelsize=p.IPC_TICK_FONT, length=0, pad=14, colors="black")
        ax.tick_params(axis="y", labelsize=p.IPC_TICK_FONT, colors="black")
        ax.set_xlim(x_apps[0] - BAR_WIDTH, avg_x + 1.5 * BAR_WIDTH)

        ax.set_ylabel("Speedup (%)\n(normalized to\nno-fusion)", fontsize=p.IPC_AXIS_LABEL_FONT,
                      fontfamily=p.FONT_FAMILY, color="black")
        ax.yaxis.set_label_coords(-0.045, 0.5)
        ax.set_ylim(0.0, ymax * step)
        ax.yaxis.set_major_locator(mticker.MultipleLocator(step))
        ax.yaxis.set_minor_locator(mticker.MultipleLocator(step / 2))
        ax.tick_params(axis="y", which="minor", length=0)
        p._apply_speedup_y_grid(ax)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
        for label in ax.get_yticklabels():
            label.set_fontfamily(p.FONT_FAMILY)
        p._draw_app_x_tick_guides(ax, x_ticks)

        legend = ax.legend(handles=p._ipc_legend_handles(handles), frameon=True, fancybox=False,
                           loc="lower center", bbox_to_anchor=(0.5, 1.04), bbox_transform=ax.transAxes,
                           fontsize=p.IPC_LEGEND_FONT, edgecolor="black", ncol=len(SERIES),
                           handlelength=0.95, handleheight=0.95, borderpad=0.55, columnspacing=1.0)
        legend.set_clip_on(False)
        legend.get_frame().set_linewidth(p.BAR_EDGE_WIDTH)
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(p.BAR_EDGE_WIDTH)

        plt.subplots_adjust(top=0.72, bottom=0.30, left=0.12, right=0.98)
        for ext in ("png", "pdf"):
            fig.savefig(out_dir / f"{stem}.{ext}", bbox_inches="tight", pad_inches=0.15, dpi=300)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scarab", type=Path, default=Path.home() / "scarab")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    out = args.out or args.scarab / RESULTS / "helios-paper-config-ifuse"

    base = read_ipc(args.scarab, *BASELINE)
    workloads = list(base)
    pct = []
    for _, label, _, ref, path, column in SERIES:
        ipc = read_ipc(args.scarab, ref, path, column)
        vals = [(ipc[w] / base[w] - 1.0) * 100.0 for w in workloads]
        pct.append(vals)
        print(f"{label}: average speedup {sum(vals) / len(vals):+.2f}%")

    plot_ipc.register_noto_serif()
    out.mkdir(parents=True, exist_ok=True)
    plot(workloads, pct, out)
    print(f"Wrote {out}/speedup.{{png,pdf}}, speedup-labeled.{{png,pdf}}")


if __name__ == "__main__":
    main()
