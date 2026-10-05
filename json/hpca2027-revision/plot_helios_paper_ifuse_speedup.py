#!/usr/bin/env python3
"""Plot I-Fuse speedup over no fusion on the Helios paper config.

Reads summary.csv files committed in the scarab repo (100M measured instructions after
20M warmup, PARAMS.helios_paper) and plots IPC_ifuse / IPC_no_fusion - 1 per app plus
the geomean, for same-cycle and 1-cycle-delayed LOAD2 wake-up.

Usage: plot_helios_paper_ifuse_speedup.py [--scarab ~/scarab] [--out <png>]
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = "src/hpca2027-revision"
BASELINE = ("origin/hpca2027-revision-baseline", f"{RESULTS}/helios-paper-config-baseline/summary.csv")
SERIES = [
    # label, git ref, path, color
    ("I-Fuse (same-cycle LOAD2 wake)", "14f33aacb", f"{RESULTS}/helios-paper-config-ifuse/summary.csv", "#2a78d6"),
    ("I-Fuse (1-cycle-delayed LOAD2 wake)", "a0565667f", f"{RESULTS}/helios-paper-config-ifuse/summary.csv", "#eb6834"),
]


def read_ipc(scarab: Path, ref: str, path: str) -> dict[str, float]:
    text = subprocess.run(["git", "-C", str(scarab), "show", f"{ref}:{path}"],
                          check=True, capture_output=True, text=True).stdout
    return {r["app"]: float(r["ipc"]) for r in csv.DictReader(io.StringIO(text))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scarab", type=Path, default=Path.home() / "scarab")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    out = args.out or args.scarab / RESULTS / "helios-paper-config-ifuse/speedup_vs_no_fusion.png"

    base = read_ipc(args.scarab, *BASELINE)
    apps = list(base)
    series = []
    for label, ref, path, color in SERIES:
        ipc = read_ipc(args.scarab, ref, path)
        sp = [ipc[a] / base[a] for a in apps]
        gm = math.exp(sum(map(math.log, sp)) / len(sp))
        series.append((label, color, [100 * (s - 1) for s in sp] + [100 * (gm - 1)]))
        print(f"{label}: geomean {100 * (gm - 1):+.2f}%")
    names = apps + ["geomean"]

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": "#52514e", "axes.labelcolor": "#0b0b0b",
                         "xtick.color": "#52514e", "ytick.color": "#52514e"})
    fig, ax = plt.subplots(figsize=(7.5, 3.0), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    width = 0.36
    for i, (label, color, vals) in enumerate(series):
        xs = [x + (i - 0.5) * (width + 0.02) for x in range(len(names))]
        ax.bar(xs, vals, width, color=color, label=f"{label}, geomean {vals[-1]:+.2f}%", zorder=3)
    ax.axhline(0, color="#52514e", linewidth=0.8, zorder=4)
    ax.axvline(len(apps) - 0.5, color="#c3c2b7", linewidth=0.8, linestyle=":")
    ax.set_xticks(range(len(names)), names, rotation=30, ha="right")
    ax.set_ylabel("Speedup over no fusion (%)")
    ax.yaxis.grid(True, color="#e4e3df", linewidth=0.6, zorder=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=1)
    lo = min(min(v) for _, _, v in series)
    hi = max(max(v) for _, _, v in series)
    pad = 0.15 * (hi - lo)
    ax.set_ylim(lo - pad, hi + 4 * pad)
    ax.set_title("I-Fuse on the Helios paper config (SPEC CPU2017 speed int, 100M after 20M warmup)",
                 fontsize=9, color="#0b0b0b", loc="left")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor())
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
