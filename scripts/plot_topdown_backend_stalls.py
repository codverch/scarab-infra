#!/usr/bin/env python3
"""Plot Scarab top-down percentages from core.stat.0.csv files.

The script recomputes top-level top-down percentages from raw slot counters
before aggregating simpoints. That keeps workload averages weighted by total
slots instead of averaging already-rounded per-simpoint percentages.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


WORKLOAD_LABELS = {
    "bc": "betweenness centrality",
    "bfs": "breadth first search",
    "cc": "connected components",
    "cd": "community detection",
    "chemcrow": "chemcrow",
    "dfs": "depth first search",
    "langchain_web": "langchain web",
    "mongodb": "mongodb",
    "mysql": "mysql",
    "memcached": "memcached",
    "pagerank": "pagerank",
    "postgres": "postgres",
    "rag_haystack": "rag haystack",
    "redis": "redis",
    "sssp_ego_fb": "single source shortest path",
    "swe_agent": "swe-agent",
    "tc": "triangle counting",
    "toolformer": "toolformer",
}

STAT_NAMES = {
    "total": "TOPDOWN_TOTAL_SLOTS_count",
    "issued": "TOPDOWN_ISSUED_SLOTS_count",
    "retired": "TOPDOWN_RETIRED_SLOTS_count",
    "fetch_bubbles": "TOPDOWN_FETCH_BUBBLES_SLOTS_count",
    "recovery_bubbles": "TOPDOWN_RECOVERY_BUBBLES_SLOTS_count",
}

METRICS = [
    ("Frontend bound", "#4c78a8"),
    ("Bad speculation", "#f58518"),
    ("Retiring", "#54a24b"),
    ("Backend bound", "#b279a2"),
]


def read_stats(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    wanted = set(STAT_NAMES.values())
    with path.open(newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 3:
                continue
            name = row[0].strip()
            if name in wanted:
                values[name] = float(row[2].strip())

    missing = sorted(wanted - values.keys())
    if missing:
        raise KeyError(f"{path} missing required stats: {', '.join(missing)}")
    return values


def infer_workload(path: Path, root: Path) -> str:
    parts = path.relative_to(root).parts
    for part in parts:
        if part in WORKLOAD_LABELS:
            return part
    if len(parts) >= 3 and parts[-2].isdigit():
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return path.parent.name


def add_percentages(row: dict[str, float]) -> None:
    total = row["total_slots"]
    if total <= 0:
        raise ValueError(f"{row['workload']} has non-positive TOPDOWN_TOTAL_SLOTS")

    frontend = row["fetch_bubbles"] / total * 100.0
    bad_spec = (row["issued"] - row["retired"] + row["recovery_bubbles"]) / total * 100.0
    retiring = row["retired"] / total * 100.0
    backend = 100.0 - frontend - bad_spec - retiring

    row["Frontend bound"] = frontend
    row["Bad speculation"] = bad_spec
    row["Retiring"] = retiring
    row["Backend bound"] = backend
    row["Topdown sum"] = frontend + bad_spec + retiring + backend


def collect(root: Path) -> list[dict[str, float]]:
    by_workload: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "simpoints": 0,
            "total_slots": 0.0,
            "issued": 0.0,
            "retired": 0.0,
            "fetch_bubbles": 0.0,
            "recovery_bubbles": 0.0,
        }
    )

    seen: dict[str, dict[tuple, Path]] = defaultdict(dict)

    for stat_path in sorted(root.rglob("core.stat.0.csv")):
        workload = infer_workload(stat_path, root)
        stats = read_stats(stat_path)
        fingerprint = tuple(stats[STAT_NAMES[key]] for key in STAT_NAMES)
        prior = seen[workload].get(fingerprint)
        if prior is not None:
            raise SystemExit(
                f"{workload}: {stat_path} and {prior} have identical top-down counters. "
                "Distinct simpoints must not produce identical stats; this usually means the "
                "measured ROI window sat in the shared startup chunk (segment_size/warmup "
                "mismatch with the trace's chunk_instr_count)."
            )
        seen[workload][fingerprint] = stat_path
        row = by_workload[workload]
        row["simpoints"] += 1
        row["total_slots"] += stats[STAT_NAMES["total"]]
        row["issued"] += stats[STAT_NAMES["issued"]]
        row["retired"] += stats[STAT_NAMES["retired"]]
        row["fetch_bubbles"] += stats[STAT_NAMES["fetch_bubbles"]]
        row["recovery_bubbles"] += stats[STAT_NAMES["recovery_bubbles"]]

    if not by_workload:
        raise SystemExit(f"No core.stat.0.csv files found under {root}")

    rows: list[dict[str, float]] = []
    average = {
        "workload": "Average",
        "label": "Average",
        "simpoints": 0,
        "total_slots": 0.0,
        "issued": 0.0,
        "retired": 0.0,
        "fetch_bubbles": 0.0,
        "recovery_bubbles": 0.0,
    }

    for workload, values in sorted(by_workload.items()):
        row = {"workload": workload, "label": WORKLOAD_LABELS.get(workload, workload.replace("_", " ")), **values}
        add_percentages(row)
        rows.append(row)
        for key in ["simpoints", "total_slots", "issued", "retired", "fetch_bubbles", "recovery_bubbles"]:
            average[key] += values[key]

    add_percentages(average)
    rows.append(average)
    return rows


def write_csv(rows: list[dict[str, float]], path: Path) -> None:
    fieldnames = [
        "workload",
        "label",
        "simpoints",
        "total_slots",
        "Frontend bound",
        "Bad speculation",
        "Retiring",
        "Backend bound",
        "Topdown sum",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fieldnames})


def plot(rows: list[dict[str, float]], out_png: Path, out_pdf: Path | None) -> None:
    labels = [str(row["label"]) for row in rows]
    x = range(len(rows))
    bottoms = [0.0] * len(rows)

    fig_width = max(12.0, len(rows) * 0.72)
    fig, ax = plt.subplots(figsize=(fig_width, 6.0))

    for metric, color in METRICS:
        values = [float(row[metric]) for row in rows]
        ax.bar(x, values, bottom=bottoms, label=metric, color=color, edgecolor="black", linewidth=0.4)
        bottoms = [bottom + value for bottom, value in zip(bottoms, values)]

    avg_idx = len(rows) - 1
    ax.axvline(avg_idx - 0.5, linestyle="--", color="#444444", linewidth=1.0, alpha=0.8)
    ax.set_ylabel("Top-down slots (%)")
    ax.set_ylim(0, 100)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=38, ha="right")
    ax.grid(axis="y", linestyle=":", linewidth=0.7, color="#777777", alpha=0.65)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.14), ncol=4, frameon=True, fancybox=False, edgecolor="black")
    fig.tight_layout()
    fig.savefig(out_png, dpi=220, bbox_inches="tight")
    if out_pdf:
        fig.savefig(out_pdf, bbox_inches="tight")


def validate(rows: list[dict[str, float]], tolerance: float) -> None:
    for row in rows:
        total = float(row["Topdown sum"])
        if abs(total - 100.0) > tolerance:
            raise ValueError(f"{row['workload']} top-down categories sum to {total:.3f}, not 100")
        if float(row["Backend bound"]) < -tolerance:
            raise ValueError(f"{row['workload']} has negative backend bound: {row['Backend bound']:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="Simulation/stat root to search recursively.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for CSV/PNG/PDF outputs.")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF output.")
    parser.add_argument("--sum-tolerance", type=float, default=0.01)
    args = parser.parse_args()

    rows = collect(args.root)
    validate(rows, args.sum_tolerance)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "topdown_backend_stalls.csv"
    png_path = args.out_dir / "topdown_backend_stalls.png"
    pdf_path = None if args.no_pdf else args.out_dir / "topdown_backend_stalls.pdf"

    write_csv(rows, csv_path)
    plot(rows, png_path, pdf_path)

    print(f"Wrote {csv_path}")
    print(f"Wrote {png_path}")
    if pdf_path:
        print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
