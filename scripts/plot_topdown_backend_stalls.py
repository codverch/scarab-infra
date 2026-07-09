#!/usr/bin/env python3
"""Plot Scarab top-down percentages from core.stat.0.csv files.

Per-simpoint top-down percentages are computed from raw slot counters, then
combined into one value per workload using SimPoint cluster weights from the
scarab-infra workload DB (weights are renormalized over the simpoints that are
actually present). This matches the SimPoint methodology: each trace result is
multiplied by its simpoint weight. The Average row is the arithmetic mean of
the per-workload values.
"""

from __future__ import annotations

import argparse
import csv
import json
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


def percentages(counters: dict[str, float]) -> dict[str, float]:
    total = counters["total"]
    if total <= 0:
        raise ValueError("non-positive TOPDOWN_TOTAL_SLOTS")

    frontend = counters["fetch_bubbles"] / total * 100.0
    bad_spec = (
        (counters["issued"] - counters["retired"] + counters["recovery_bubbles"])
        / total
        * 100.0
    )
    retiring = counters["retired"] / total * 100.0
    backend = 100.0 - frontend - bad_spec - retiring
    return {
        "Frontend bound": frontend,
        "Bad speculation": bad_spec,
        "Retiring": retiring,
        "Backend bound": backend,
        "Topdown sum": frontend + bad_spec + retiring + backend,
    }


def load_weights(
    db_path: Path, suite: str, subsuite: str
) -> dict[str, dict[str, float]]:
    """Return workload -> cluster_id -> weight for one suite/subsuite."""
    data = json.loads(db_path.read_text())
    try:
        workloads = data[suite][subsuite]
    except KeyError as exc:
        raise SystemExit(
            f"{db_path}: missing workload DB section {suite}/{subsuite}"
        ) from exc

    return {
        workload: {
            str(sp["cluster_id"]): float(sp["weight"])
            for sp in entry["simpoints"]
        }
        for workload, entry in workloads.items()
        if isinstance(entry, dict) and "simpoints" in entry
    }


def collect(
    root: Path, weights_db: dict[str, dict[str, float]]
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    per_simpoint: dict[str, list[tuple[str, dict[str, float]]]] = defaultdict(list)
    seen: dict[str, dict[tuple, Path]] = defaultdict(dict)

    for stat_path in sorted(root.rglob("core.stat.0.csv")):
        workload = infer_workload(stat_path, root)
        cluster = stat_path.parent.name
        stats = read_stats(stat_path)
        counters = {key: stats[name] for key, name in STAT_NAMES.items()}

        fingerprint = tuple(counters[key] for key in STAT_NAMES)
        prior = seen[workload].get(fingerprint)
        if prior is not None:
            raise SystemExit(
                f"{workload}: {stat_path} and {prior} have identical top-down counters. "
                "Distinct simpoints must not produce identical stats; this usually means the "
                "measured ROI window sat in a shared trace chunk (segment_size/window "
                "mismatch with the trace's chunk_instr_count)."
            )
        seen[workload][fingerprint] = stat_path
        per_simpoint[workload].append((cluster, counters))

    if not per_simpoint:
        raise SystemExit(f"No core.stat.0.csv files found under {root}")

    rows: list[dict[str, float]] = []
    simpoint_rows: list[dict[str, float]] = []
    for workload, entries in sorted(per_simpoint.items()):
        wl_weights = weights_db.get(workload)
        if wl_weights is None:
            raise SystemExit(f"{workload}: no simpoints/weights entry in the workload DB")
        missing = [cluster for cluster, _ in entries if cluster not in wl_weights]
        if missing:
            raise SystemExit(
                f"{workload}: no SimPoint weight for simpoint(s) {', '.join(sorted(missing))}; "
                f"DB lists {sorted(wl_weights)}"
            )

        norm = sum(wl_weights[cluster] for cluster, _ in entries)
        if norm <= 0:
            raise SystemExit(f"{workload}: present SimPoint weights sum to {norm}")

        workload_simpoints: list[dict[str, float]] = []
        for cluster, counters in sorted(entries, key=lambda entry: int(entry[0])):
            pct = percentages(counters)
            simpoint_row = {
                "workload": workload,
                "label": WORKLOAD_LABELS.get(
                    workload, workload.replace("_", " ")
                ),
                "cluster_id": cluster,
                "weight": wl_weights[cluster],
                "normalized_weight": wl_weights[cluster] / norm,
                "total_slots": counters["total"],
                "issued_slots": counters["issued"],
                "retired_slots": counters["retired"],
                "fetch_bubbles_slots": counters["fetch_bubbles"],
                "recovery_bubbles_slots": counters["recovery_bubbles"],
                **pct,
            }
            workload_simpoints.append(simpoint_row)
            simpoint_rows.append(simpoint_row)

        row: dict[str, float] = {
            "workload": workload,
            "label": WORKLOAD_LABELS.get(workload, workload.replace("_", " ")),
            "simpoints": len(entries),
            "total_slots": sum(counters["total"] for _, counters in entries),
        }
        for metric, _ in METRICS:
            row[metric] = sum(
                simpoint["normalized_weight"] * simpoint[metric]
                for simpoint in workload_simpoints
            )
        row["Topdown sum"] = sum(row[metric] for metric, _ in METRICS)
        rows.append(row)

    average = {
        "workload": "Average",
        "label": "Average",
        "simpoints": sum(row["simpoints"] for row in rows),
        "total_slots": sum(row["total_slots"] for row in rows),
    }
    for metric, _ in METRICS:
        average[metric] = sum(row[metric] for row in rows) / len(rows)
    average["Topdown sum"] = sum(average[metric] for metric, _ in METRICS)
    rows.append(average)
    return rows, simpoint_rows


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
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fieldnames})


def write_simpoint_csv(rows: list[dict[str, float]], path: Path) -> None:
    fieldnames = [
        "workload",
        "label",
        "cluster_id",
        "weight",
        "normalized_weight",
        "total_slots",
        "issued_slots",
        "retired_slots",
        "fetch_bubbles_slots",
        "recovery_bubbles_slots",
        "Frontend bound",
        "Bad speculation",
        "Retiring",
        "Backend bound",
        "Topdown sum",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
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
    parser.add_argument(
        "--weights-db",
        type=Path,
        required=True,
        help="workloads DB JSON (e.g. workloads/workloads_db.json) providing SimPoint cluster weights.",
    )
    parser.add_argument("--suite", default="datacenter")
    parser.add_argument("--subsuite", default="datacenter")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF output.")
    parser.add_argument("--sum-tolerance", type=float, default=0.01)
    args = parser.parse_args()

    weights = load_weights(args.weights_db, args.suite, args.subsuite)
    rows, simpoint_rows = collect(args.root, weights)
    validate(rows, args.sum_tolerance)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "topdown_backend_stalls.csv"
    simpoint_csv_path = args.out_dir / "topdown_backend_stalls_per_simpoint.csv"
    png_path = args.out_dir / "topdown_backend_stalls.png"
    pdf_path = None if args.no_pdf else args.out_dir / "topdown_backend_stalls.pdf"

    write_csv(rows, csv_path)
    write_simpoint_csv(simpoint_rows, simpoint_csv_path)
    plot(rows, png_path, pdf_path)

    print(f"Wrote {csv_path}")
    print(f"Wrote {simpoint_csv_path}")
    print(f"Wrote {png_path}")
    if pdf_path:
        print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
