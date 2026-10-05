#!/usr/bin/env python3
"""Plot ideal-fusion speedup over no fusion on the Helios paper config.

Reads <results>/summary.csv (written by package_helios_paper_ideal_fusion_results.py)
and draws it in the style of hpca2027-main-graphs/plot_ipc.py's speedup bars.
Average is the arithmetic mean of the per-app speedups, as in plot_ipc.py.

Next to our Average it draws the OracleFusion speedup the Helios paper reports
(Singh et al., MICRO'22, Sec. V-B: "OracleFusion ... stands at 16.3%
improvement"). That number is the paper's average over its SPEC CPU2017 and
MiBench workloads on RISC-V, and its OracleFusion also fuses the non-memory
idioms of its Table I, so it is a reference point, not a like-for-like bar.

Writes <results>/speedup.{png,pdf} and speedup-labeled.{png,pdf}.

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
HELIOS_PAPER_ORACLE_PCT = 16.3
SERIES = (
    ("ideal_fusion", "Ideal fusion (ours)", plot_ipc.IDEAL_FUSION_COLOR),
    ("paper_oracle", "Helios paper OracleFusion (reported avg.)", plot_ipc.HELIOS_COLOR),
)
BAR_WIDTH = 4.5


def plot(workloads: list[str], pct: list[float], out_dir: Path) -> None:
    import matplotlib.pyplot as plt

    p = plot_ipc
    avg_pct = sum(pct) / len(pct)
    x_apps = [i * p.APP_STEP for i in range(len(workloads))]
    # Average column holds two bars: ours and the paper's OracleFusion.
    avg_x = x_apps[-1] + BAR_WIDTH / 2 + p.AVERAGE_GAP + BAR_WIDTH
    separator_x = x_apps[-1] + BAR_WIDTH / 2 + p.AVERAGE_GAP * p.AVERAGE_SEPARATOR_FRAC
    x_ticks = x_apps + [avg_x]
    labels = [APP_LABELS.get(w, w) for w in workloads] + ["Average"]
    top = max(max(pct), HELIOS_PAPER_ORACLE_PCT)

    for stem, labeled in (("speedup-labeled", True), ("speedup", False)):
        p._apply_ipc_plot_style()
        fig, ax = plt.subplots(figsize=p.IPC_FIGSIZE)
        ours = ax.bar(x_apps + [avg_x - BAR_WIDTH / 2], pct + [avg_pct], BAR_WIDTH,
                      color=SERIES[0][2], edgecolor="black", linewidth=p.BAR_EDGE_WIDTH, zorder=3)
        paper = ax.bar([avg_x + BAR_WIDTH / 2], [HELIOS_PAPER_ORACLE_PCT], BAR_WIDTH,
                       color=SERIES[1][2], edgecolor="black", linewidth=p.BAR_EDGE_WIDTH, zorder=3)
        if labeled:
            p._annotate_ipc_bar_labels(ax, ours, pct + [avg_pct], fontsize=p.IPC_AXIS_FONT)
            p._annotate_ipc_bar_labels(ax, paper, [HELIOS_PAPER_ORACLE_PCT], fontsize=p.IPC_AXIS_FONT)

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

    plot_ipc.register_noto_serif()
    plot(workloads, pct, args.results)
    print(f"Average speedup {sum(pct) / len(pct):+.2f}% (Helios paper OracleFusion: +{HELIOS_PAPER_ORACLE_PCT}%)")
    print(f"Wrote {args.results}/speedup.{{png,pdf}}, speedup-labeled.{{png,pdf}}")


if __name__ == "__main__":
    main()
