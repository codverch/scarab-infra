#!/usr/bin/env python3
"""Paper-style backend stall breakdown (RAT / ROB / LQ / SQ), ROB 352 vs 512.

Reuses the canvas, Helios colors and styling of
hpca2027-characterization/plot_backend_resource_stalls.py: two stacked bars
per app (ROB 352 solid, ROB 512 hatched), same attribution of allocation
stall cycles. Data comes from the packaged results
(<results>/rob-<N>/<app>/core.stat.0.csv).

Writes <results>/backend_stalls/backend-resource-stalls.{png,pdf}.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))
_spec = importlib.util.spec_from_file_location(
    "plot_backend_resource_stalls",
    INFRA / "hpca2027-characterization" / "plot_backend_resource_stalls.py",
)
brs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = brs  # dataclasses resolve types via sys.modules
_spec.loader.exec_module(brs)

APP_LABELS = {
    "gcc_s": "gcc-1",
    "gcc_s_2": "gcc-2",
    "gcc_s_3": "gcc-3",
    "leela_s": "leela",
    "mcf_s": "mcf",
    "omnetpp_s": "omnetpp",
    "xalancbmk_s": "xalancbmk",
}


SEGMENT_EDGE_WIDTH = 1.5  # between stacked segments; outer outline keeps BAR_EDGE_WIDTH
SQ_LABEL_FONT = 70


def plot_breakdown(results, output_dir: Path) -> None:
    """brs.plot_breakdown with thin edges between segments, so the <1% ROB /
    LQ / SQ segments are not swallowed by the thick outline, and the SQ
    percentage printed above each bar."""
    np, plt, mticker = brs.np, brs.plt, brs.mticker
    rows = results + [brs.average_result(results)]
    labels = [brs.display_name(r.workload) for r in rows]
    x_apps = np.arange(len(results), dtype=float) * brs.APP_STEP
    cluster_half = brs.BAR_OFFSET + brs.BAR_WIDTH / 2.0
    avg_x = float(x_apps[-1] + 2.0 * cluster_half + brs.AVERAGE_GAP)
    x = np.append(x_apps, avg_x)
    separator_x = float(x_apps[-1] + cluster_half + brs.AVERAGE_GAP * 0.5)
    segments = [(f, c) for f, c in brs.BREAKDOWN_SEGMENTS if f != "other_pct"]

    brs._apply_plot_style()
    fig, ax = plt.subplots(figsize=brs.FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for (cfg, _, hatch), offset in zip(brs.CONFIGS, (-brs.BAR_OFFSET, brs.BAR_OFFSET)):
        bottoms = np.zeros(len(rows))
        for field, color in segments:
            values = np.array([getattr(r.breakdowns[cfg], field) for r in rows])
            ax.bar(x + offset, values, brs.BAR_WIDTH, bottom=bottoms, color=color,
                   edgecolor="black", linewidth=SEGMENT_EDGE_WIDTH, hatch=hatch, zorder=3)
            bottoms += values
        ax.bar(x + offset, bottoms, brs.BAR_WIDTH, fill=False, edgecolor="black",
               linewidth=brs.BAR_EDGE_WIDTH, zorder=4)
        for xi, top, r in zip(x + offset, bottoms, rows):
            sq = r.breakdowns[cfg].sq_pct
            ax.text(xi, top + 1.2, f"{sq:.1f}", ha="center", va="bottom", rotation=90,
                    fontsize=SQ_LABEL_FONT, fontfamily=brs.plot_ipc.FONT_FAMILY, color="black",
                    zorder=5)

    ax.axvline(x=separator_x, color=brs.AVERAGE_SEPARATOR_COLOR, linestyle="--",
               linewidth=brs.AVERAGE_SEPARATOR_WIDTH, zorder=2)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", color="black")
    for label in ax.get_xticklabels():
        label.set_fontsize(brs.AXIS_FONT)
        label.set_fontfamily(brs.plot_ipc.FONT_FAMILY)
        if label.get_text() == "Average":
            label.set_weight("bold")
    ax.set_xlim(x[0] - cluster_half - 2.5, x[-1] + cluster_half + 2.5)
    ax.margins(x=0)
    ax.set_ylabel(brs.Y_AXIS_LABEL, fontsize=brs.AXIS_LABEL_FONT,
                  fontfamily=brs.plot_ipc.FONT_FAMILY, color="black", labelpad=brs.Y_LABEL_PAD)
    ax.set_ylim(0.0, brs.Y_MAX)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=brs.AXIS_FONT, length=0, pad=14, colors="black")
    ax.tick_params(axis="y", labelsize=brs.AXIS_FONT, colors="black")
    for label in ax.get_yticklabels():
        label.set_fontfamily(brs.plot_ipc.FONT_FAMILY)

    plt.subplots_adjust(top=0.62, bottom=0.30, left=0.12, right=0.98)
    brs._style_legend(ax, segments)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(brs.BAR_EDGE_WIDTH)
    ax.text(1.0, 1.02, "Numbers above bars: SQ stall cycles (%)", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=SQ_LABEL_FONT, fontfamily=brs.plot_ipc.FONT_FAMILY)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{brs.OUTPUT_STEM}.{ext}", dpi=brs.OUTPUT_DPI,
                    bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def load_counts(path: Path) -> dict:
    counts = dict.fromkeys(brs.STATS, 0.0)
    for stat in brs.STATS:
        counts[stat] = float(brs.stat_count_from_csv(path, stat) or 0.0)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--apps", nargs="+", required=True)
    args = ap.parse_args()

    robs = sorted((p.name.split("-")[1] for p in args.results.glob("rob-*")), key=int)
    brs.CONFIGS = tuple(
        (f"rob{r}", f"ROB {r}", "" if i == 0 else "//") for i, r in enumerate(robs)
    )
    brs.BREAKDOWN_CATEGORIES.update(
        reg_file_pct="RAT", rob_pct="ROB", lq_pct="LQ", sq_pct="SQ"
    )
    brs.display_name = lambda w: "Average" if w == "Average" else APP_LABELS.get(w, w)
    brs.Y_MAX = 100.0
    brs.OUTPUT_STEM = "backend-resource-stalls"

    results = []
    for app in args.apps:
        breakdowns = {}
        for r in robs:
            b = brs.breakdown_from_counts(load_counts(args.results / f"rob-{r}" / app / "core.stat.0.csv"))
            if b is None:
                raise SystemExit(f"no cycles for rob-{r}/{app}")
            breakdowns[f"rob{r}"] = b
        results.append(brs.WorkloadResult(workload=app, trace_count=1, breakdowns=breakdowns))

    brs.register_noto_serif()
    out = args.results / "backend_stalls"
    plot_breakdown(results, out)
    print(f"Wrote {out}/{brs.OUTPUT_STEM}.{{png,pdf}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
