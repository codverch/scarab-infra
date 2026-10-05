#!/usr/bin/env python3
"""Plot ideal-fusion speedup over no fusion on the Helios paper config.

Reads <results>/summary.csv (written by package_helios_paper_ideal_fusion_results.py)
and draws it in the style of hpca2027-main-graphs/plot_ipc.py's speedup bars.
Average is the arithmetic mean of the per-app speedups, as in plot_ipc.py.

Next to each of our bars it draws the OracleFusion speedup the Helios paper
reports for the same benchmark (Singh et al., MICRO'22, Figure 10). The paper
prints no per-app numbers, so these were read from the figure's vector
geometry: bar tops calibrated on its 0.8-1.4 gridlines. The digitized averages
reproduce the paper's text (Helios 14.2%, OracleFusion 16.3%). The paper ran
RISC-V binaries on its own simulator and its OracleFusion also fuses the
non-memory idioms of its Table I, so its bars are a reference point, not a
like-for-like comparison. The values are written to <results>/helios_paper_oracle.csv.

Writes <results>/speedup.{png,pdf}, speedup-labeled.{png,pdf} and helios_paper_oracle.csv.

Usage: plot_helios_paper_ideal_fusion_speedup.py \
           [--results ~/scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))
import plot_ipc  # noqa: E402

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
# Helios paper Figure 10 OracleFusion normalized IPC, digitized from the PDF.
HELIOS_PAPER_ORACLE_IPC = {
    "deepsjeng_s": ("631.deepsjeng", 1.024),
    "exchange2_s": ("648.exchange2", 1.152),
    "gcc_s": ("602.gcc_1", 1.177),
    "gcc_s_2": ("602.gcc_2", 1.175),
    "gcc_s_3": ("602.gcc_3", 1.176),
    "leela_s": ("641.leela", 1.062),
    "mcf_s": ("605.mcf", 0.984),
    "omnetpp_s": ("620.omnetpp", 1.105),
    "xalancbmk_s": ("623.xalancbmk", 1.055),
}
SERIES = (
    ("ideal_fusion", "Ideal fusion (ours)", plot_ipc.IDEAL_FUSION_COLOR),
    ("paper_oracle", "Helios paper OracleFusion (Fig. 10)", plot_ipc.HELIOS_COLOR),
)
BAR_WIDTH = 2.6


def plot(workloads: list[str], pct: list[float], paper_pct: list[float], out_dir: Path) -> None:
    import matplotlib.pyplot as plt

    p = plot_ipc
    ours_all = pct + [sum(pct) / len(pct)]
    paper_all = paper_pct + [sum(paper_pct) / len(paper_pct)]
    x_apps = [i * p.APP_STEP for i in range(len(workloads))]
    avg_x = x_apps[-1] + BAR_WIDTH + p.AVERAGE_GAP + BAR_WIDTH
    separator_x = x_apps[-1] + BAR_WIDTH + p.AVERAGE_GAP * p.AVERAGE_SEPARATOR_FRAC
    x_ticks = x_apps + [avg_x]
    labels = [APP_LABELS.get(w, w) for w in workloads] + ["Average"]
    top = max(ours_all + paper_all)

    for stem, labeled in (("speedup-labeled", True), ("speedup", False)):
        p._apply_ipc_plot_style()
        fig, ax = plt.subplots(figsize=p.IPC_FIGSIZE)
        # As in plot_ipc.py, negative speedups draw at zero and keep their label.
        for lane, ((_, _, color), vals, off) in enumerate(
                zip(SERIES, (ours_all, paper_all), (-BAR_WIDTH / 2, BAR_WIDTH / 2))):
            bars = ax.bar([x + off for x in x_ticks], [max(0.0, v) for v in vals], BAR_WIDTH,
                          color=color, edgecolor="black", linewidth=p.BAR_EDGE_WIDTH, zorder=3)
            if labeled:
                p._annotate_ipc_bar_labels(ax, bars, vals, fontsize=p.IPC_AXIS_FONT,
                                           label_lane=lane, n_label_lanes=len(SERIES))
                # plot_ipc's labeler skips negatives; label them just above the axis.
                for x, v in zip(x_ticks, vals):
                    if v < 0:
                        ax.text(x + off, 0.6, f"{v:.1f}", ha="center", va="bottom", rotation=90,
                                fontsize=p.IPC_AXIS_FONT, fontfamily=p.FONT_FAMILY, color="black",
                                zorder=4)

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
        ax.set_ylim(0.0, (int(top / 5) + 1) * 5 + (5.0 if labeled else 0.0))
        p._apply_speedup_y_ticks(ax)
        p._apply_speedup_y_grid(ax)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
        for label in ax.get_yticklabels():
            label.set_fontfamily(p.FONT_FAMILY)
        p._draw_app_x_tick_guides(ax, x_ticks)

        legend = ax.legend(handles=p._ipc_legend_handles(SERIES), frameon=True, fancybox=False,
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
    ap.add_argument("--results", type=Path,
                    default=Path.home() / "scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion")
    args = ap.parse_args()

    with open(args.results / "summary.csv") as f:
        rows = list(csv.DictReader(f))
    workloads = [r["app"] for r in rows]
    pct = [(float(r["speedup"]) - 1.0) * 100.0 for r in rows]
    paper_pct = [(HELIOS_PAPER_ORACLE_IPC[w][1] - 1.0) * 100.0 for w in workloads]

    with open(args.results / "helios_paper_oracle.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["app", "helios_paper_benchmark", "paper_oracle_norm_ipc", "paper_oracle_speedup_pct",
                    "ours_ideal_fusion_speedup_pct", "source"])
        for app, ours, paper in zip(workloads, pct, paper_pct):
            w.writerow([app, HELIOS_PAPER_ORACLE_IPC[app][0], f"{HELIOS_PAPER_ORACLE_IPC[app][1]:.3f}",
                        f"{paper:.1f}", f"{ours:.2f}", "Singh et al. MICRO'22 Fig. 10, digitized"])

    plot_ipc.register_noto_serif()
    plot(workloads, pct, paper_pct, args.results)
    print(f"Average speedup: ours {sum(pct) / len(pct):+.2f}%, Helios paper OracleFusion "
          f"{sum(paper_pct) / len(paper_pct):+.2f}% (same 9 apps)")
    print(f"Wrote {args.results}/speedup.{{png,pdf}}, speedup-labeled.{{png,pdf}}")


if __name__ == "__main__":
    main()
