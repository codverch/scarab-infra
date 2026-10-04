#!/usr/bin/env python3
"""Top-down breakdown plots for the hpca2027-revision baseline (ROB 352 vs 512).

Reads Scarab's TOPDOWN_*_BOUND counters (scaled so 10000 = 100%) from
<results>/rob-<N>/<app>/core.stat.0.csv and writes to <results>/topdown/:

    topdown_level1.{png,pdf}   Retiring / Frontend / Bad speculation / Backend
    topdown_level2.{png,pdf}   each category split one level down
    topdown.csv                every percentage plotted
"""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

SCALE = 100.0  # TOPDOWN_SCALE_FACTOR 10000 -> percent

# (label, stat) in stack order, bottom to top.
LEVEL1 = [
    ("Retiring", "TOPDOWN_RETIRING_BOUND"),
    ("Frontend bound", "TOPDOWN_FRONTEND_BOUND"),
    ("Bad speculation", "TOPDOWN_BAD_SPEC_BOUND"),
    ("Backend bound", "TOPDOWN_BACKEND_BOUND"),
]
LEVEL2 = [
    ("Retiring", "TOPDOWN_RETIRING_BOUND"),
    ("Fetch latency", "TOPDOWN_FETCH_LATENCY_BOUND"),
    ("Fetch bandwidth", "TOPDOWN_FETCH_BANDWIDTH_BOUND"),
    ("Branch mispredict", "TOPDOWN_BR_RECOVER_AT_EXEC_BOUND"),
    ("Machine clears", "TOPDOWN_MACHINE_CLEARS_BOUND"),
    ("Memory bound", "TOPDOWN_MEM_BOUND"),
    ("Core bound", "TOPDOWN_CORE_BOUND"),
]
# Reference categorical palette, fixed order; Retiring is neutral in level 2
# so the six stall sub-categories get the six categorical slots.
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
NEUTRAL = "#b5b3ad"
LEVEL1_COLORS = CATEGORICAL[:4]
LEVEL2_COLORS = [NEUTRAL] + CATEGORICAL

TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"
SURFACE = "#ffffff"


def read_bounds(path: Path) -> dict:
    out = {}
    with path.open() as f:
        for row in csv.reader(f):
            if len(row) >= 3 and row[0].startswith("TOPDOWN_") and row[0].endswith("_BOUND_count"):
                out[row[0][: -len("_count")]] = int(row[2]) / SCALE
    return out


def plot(data, apps, robs, levels, colors, title, out_stem: Path):
    groups = apps + ["Average"]
    bar_w, gap = 0.38, 0.04
    fig, ax = plt.subplots(figsize=(12.5, 4.6))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for gi, app in enumerate(groups):
        for ri, rob in enumerate(robs):
            x = gi + (ri - 0.5) * (bar_w + gap)
            bottom = 0.0
            for (label, stat), color in zip(levels, colors):
                if app == "Average":
                    v = sum(data[rob][a][stat] for a in apps) / len(apps)
                else:
                    v = data[rob][app][stat]
                # white edge = 2px surface gap between stacked segments
                ax.bar(x, v, bar_w, bottom=bottom, color=color, edgecolor=SURFACE, linewidth=1.5)
                bottom += v
            ax.text(x, -2.5, rob, ha="center", va="top", fontsize=8.5, color=TEXT_MUTED)

    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups, fontsize=10.5, color=TEXT)
    ax.tick_params(axis="x", length=0, pad=16)
    ax.set_xlim(-0.6, len(groups) - 0.4)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Pipeline slots (%)", fontsize=10.5, color=TEXT)
    ax.tick_params(axis="y", labelsize=9.5, colors=TEXT_MUTED)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)

    handles = [Patch(facecolor=c, label=l) for (l, _), c in zip(levels, colors)]
    ax.legend(handles=handles[::-1], loc="upper left", bbox_to_anchor=(1.01, 1.0),
              frameon=False, fontsize=9.5, labelcolor=TEXT)
    ax.set_title(title, loc="left", fontsize=12, color=TEXT, pad=10)
    fig.text(0.01, 0.01, "Bars per benchmark: ROB size (entries). Golden Cove, 500M instructions, no warmup.",
             fontsize=8.5, color=TEXT_MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(out_stem.with_suffix(f".{ext}"), dpi=200)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--apps", nargs="+", required=True)
    args = ap.parse_args()

    robs = sorted((p.name.split("-")[1] for p in args.results.glob("rob-*")), key=int)
    data = {r: {a: read_bounds(args.results / f"rob-{r}" / a / "core.stat.0.csv") for a in args.apps}
            for r in robs}
    out = args.results / "topdown"
    out.mkdir(exist_ok=True)

    plot(data, args.apps, robs, LEVEL1, LEVEL1_COLORS, "Top-down breakdown (level 1)", out / "topdown_level1")
    plot(data, args.apps, robs, LEVEL2, LEVEL2_COLORS, "Top-down breakdown (level 2)", out / "topdown_level2")

    stats = [s for _, s in LEVEL1] + [s for _, s in LEVEL2 if s not in dict(LEVEL1).values()]
    names = {s: l for l, s in LEVEL1 + LEVEL2}
    with (out / "topdown.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", "rob"] + [names[s] for s in stats])
        for a in args.apps:
            for r in robs:
                w.writerow([a, r] + [f"{data[r][a][s]:.2f}" for s in stats])
    print(f"Wrote {out}/topdown_level1.{{png,pdf}}, topdown_level2.{{png,pdf}}, topdown.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
