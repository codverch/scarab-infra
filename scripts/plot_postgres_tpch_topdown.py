#!/usr/bin/env python3
"""Create an auditable single-workload top-down breakdown from Scarab stats."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_topdown_backend_stalls import METRICS, STAT_NAMES, percentages, read_stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-stat", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workload", default="postgres_tpch_sf10_q7")
    args = parser.parse_args()

    stats = read_stats(args.core_stat)
    counters = {name: stats[stat] for name, stat in STAT_NAMES.items()}
    values = percentages(counters)
    if values["Backend bound"] < 0 or abs(values["Topdown sum"] - 100.0) > 0.01:
        raise SystemExit(f"Invalid top-down breakdown: {values}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "raw_topdown_counters.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["counter", "value"])
        writer.writerows(counters.items())

    with (args.out_dir / "topdown_breakdown.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["workload", *(metric for metric, _ in METRICS), "Topdown sum"])
        writer.writerow([args.workload, *(values[metric] for metric, _ in METRICS), values["Topdown sum"]])

    fig, ax = plt.subplots(figsize=(4.8, 5.2))
    bottom = 0.0
    for metric, color in METRICS:
        value = values[metric]
        ax.bar([0], [value], bottom=[bottom], color=color, edgecolor="black", linewidth=0.5, label=metric)
        if value >= 4.0:
            ax.text(0, bottom + value / 2, f"{value:.1f}%", ha="center", va="center", fontsize=10)
        bottom += value
    ax.set_xticks([0])
    ax.set_xticklabels(["PostgreSQL\nTPC-H SF10 Q7"])
    ax.set_ylabel("Top-down slots (%)")
    ax.set_ylim(0, 100)
    ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.65)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.18), ncol=2, frameon=True, fancybox=False)
    fig.tight_layout()
    fig.savefig(args.out_dir / "topdown_breakdown.png", dpi=220, bbox_inches="tight")
    fig.savefig(args.out_dir / "topdown_breakdown.pdf", bbox_inches="tight")
    print({metric: values[metric] for metric, _ in METRICS})


if __name__ == "__main__":
    main()
