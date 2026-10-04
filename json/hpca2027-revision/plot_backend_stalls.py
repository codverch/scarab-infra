#!/usr/bin/env python3
"""Backend resource stall breakdown for the hpca2027-revision baseline (ROB 352 vs 512).

Same attribution as hpca2027-characterization/plot_backend_resource_stalls.py:
each bar stacks the % of cycles in which rename/allocation is blocked, by the
backend resource that blocked it (core.stat.0.csv):

  RAT       = MAP_STAGE_STALL_ITSELF (rename blocked: no free physical register)
  ROB/LQ/SQ = MAP_STAGE_STALLED split in proportion to
              FULL_WINDOW_STALL, LSQ_FULL_LOAD_QUEUE, LSQ_FULL_STORE_QUEUE
  Other     = MAP_STAGE_STALLED not covered by those counters (CSV only;
              0 in every run)

Scarab never blocks allocation on a full issue queue (ops wait in the ROB),
so there is no IQ component.

Writes to <results>/backend_stalls/:
    backend_stalls.{png,pdf}            RAT / ROB / LQ / SQ stacked
    backend_stalls_rob_lq_sq.{png,pdf}  ROB, LQ, SQ zoomed (one panel each)
    backend_stalls.csv
"""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

STATS = ("NODE_CYCLE", "MAP_STAGE_STALL_ITSELF", "MAP_STAGE_STALLED",
         "FULL_WINDOW_STALL", "LSQ_FULL_LOAD_QUEUE", "LSQ_FULL_STORE_QUEUE")
# (label, color) in stack order, bottom to top. Reference categorical palette,
# fixed order.
SEGMENTS = [
    ("RAT", "#2a78d6"),
    ("ROB", "#eb6834"),
    ("LQ", "#1baf7a"),
    ("SQ", "#eda100"),
]
CSV_ONLY = ["Other"]
TEXT, TEXT_MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"


def read_counts(path: Path) -> dict:
    counts = dict.fromkeys(STATS, 0)
    with path.open() as f:
        for row in csv.reader(f):
            if len(row) >= 3 and row[0].endswith("_count") and row[0][:-6] in counts:
                counts[row[0][:-6]] = int(row[2])
    return counts


def breakdown(c: dict) -> list:
    cycles = c["NODE_CYCLE"]
    downstream = c["MAP_STAGE_STALLED"]
    rob, lq, sq = c["FULL_WINDOW_STALL"], c["LSQ_FULL_LOAD_QUEUE"], c["LSQ_FULL_STORE_QUEUE"]
    attributed = rob + lq + sq
    # ROB/LSQ-full counters can also fire while map is register-stalled,
    # so scale them onto MAP_STAGE_STALLED.
    scale = min(1.0, downstream / attributed) if attributed else 0.0
    other = max(0, downstream - attributed)
    return [100.0 * v / cycles for v in
            (c["MAP_STAGE_STALL_ITSELF"], rob * scale, lq * scale, sq * scale, other)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--apps", nargs="+", required=True)
    args = ap.parse_args()

    robs = sorted((p.name.split("-")[1] for p in args.results.glob("rob-*")), key=int)
    data = {r: {a: breakdown(read_counts(args.results / f"rob-{r}" / a / "core.stat.0.csv"))
                for a in args.apps} for r in robs}
    for r in robs:
        data[r]["Average"] = [sum(data[r][a][i] for a in args.apps) / len(args.apps)
                              for i in range(len(SEGMENTS) + len(CSV_ONLY))]
    groups = args.apps + ["Average"]
    out = args.results / "backend_stalls"
    out.mkdir(exist_ok=True)

    with (out / "backend_stalls.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", "rob"] + [f"{l} (% cycles)" for l in [l for l, _ in SEGMENTS] + CSV_ONLY]
                   + ["Total (% cycles)"])
        for g in groups:
            for r in robs:
                vals = data[r][g]
                w.writerow([g, r] + [f"{v:.2f}" for v in vals] + [f"{sum(vals):.2f}"])

    bar_w, gap = 0.38, 0.04
    fig, ax = plt.subplots(figsize=(12.5, 4.6))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    n = len(SEGMENTS)
    ymax = max(sum(data[r][g][:n]) for r in robs for g in groups)
    for gi, g in enumerate(groups):
        for ri, r in enumerate(robs):
            x = gi + (ri - 0.5) * (bar_w + gap)
            bottom = 0.0
            for v, (_, color) in zip(data[r][g][:n], SEGMENTS):
                ax.bar(x, v, bar_w, bottom=bottom, color=color, edgecolor=SURFACE, linewidth=1.5)
                bottom += v
            ax.text(x, bottom + ymax * 0.01, f"{bottom:.0f}", ha="center", va="bottom",
                    fontsize=8.5, color=TEXT_MUTED)
            ax.text(x, -ymax * 0.025, r, ha="center", va="top", fontsize=8.5, color=TEXT_MUTED)

    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups, fontsize=10.5, color=TEXT)
    ax.tick_params(axis="x", length=0, pad=16)
    ax.set_xlim(-0.6, len(groups) - 0.4)
    ax.set_ylim(0, ymax * 1.08)
    ax.set_ylabel("Cycles allocation is blocked (%)", fontsize=10.5, color=TEXT)
    ax.tick_params(axis="y", labelsize=9.5, colors=TEXT_MUTED)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    handles = [Patch(facecolor=c, label=l) for l, c in SEGMENTS]
    ax.legend(handles=handles[::-1], loc="upper left", bbox_to_anchor=(1.01, 1.0),
              frameon=False, fontsize=9.5, labelcolor=TEXT)
    ax.set_title("Backend stalls by resource", loc="left", fontsize=12, color=TEXT, pad=10)
    fig.text(0.01, 0.01, "Bars per benchmark: ROB size (entries); number above bar = total %. "
             "Golden Cove, 500M instructions, no warmup.", fontsize=8.5, color=TEXT_MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(out / f"backend_stalls.{ext}", dpi=200)
    plt.close(fig)

    # ROB / LQ / SQ are < ~1% of cycles and vanish in the stacked chart:
    # one small panel each, own y-scale, ROB 352 vs 512 side by side.
    rob_colors = {robs[0]: "#b5b3ad", robs[-1]: "#52514e"}
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.6), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, idx in zip(axes, (1, 2, 3)):
        ax.set_facecolor(SURFACE)
        label = SEGMENTS[idx][0]
        for ri, r in enumerate(robs):
            xs = [gi + (ri - 0.5) * (bar_w + gap) for gi in range(len(groups))]
            ax.bar(xs, [data[r][g][idx] for g in groups], bar_w, color=rob_colors[r],
                   edgecolor=SURFACE, linewidth=1.5, label=f"ROB {r}")
        ax.set_title(f"{label} full", loc="left", fontsize=11, color=TEXT)
        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels(groups, rotation=40, ha="right", fontsize=9, color=TEXT)
        ax.tick_params(axis="x", length=0)
        ax.tick_params(axis="y", labelsize=9, colors=TEXT_MUTED)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
    axes[0].set_ylabel("Cycles allocation is blocked (%)", fontsize=10, color=TEXT)
    axes[-1].legend(loc="upper right", frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"backend_stalls_rob_lq_sq.{ext}", dpi=200)
    plt.close(fig)
    print(f"Wrote {out}/backend_stalls.{{png,pdf}}, backend_stalls_rob_lq_sq.{{png,pdf}}, backend_stalls.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
