#!/usr/bin/env python3
"""Build and plot PGO FCT storage vs IPC Pareto curves for scarab-infra experiments."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKLOADS_DB = REPO_ROOT / "workloads" / "workloads_db.json"
DEFAULT_BITS_PER_ENTRY = 108
DEFAULT_FREQUENCIES = [1, 10, 100, 1000, 10000, 100000]
DEFAULT_PGO_WORKLOADS = [
    "bc",
    "bfs",
    "cc",
    "cd",
    "dfs",
    "pagerank",
    "sssp_ego_fb",
    "tc",
]
FREQ_TO_CONFIG = {
    1: "pgo_freq_1",
    10: "pgo_freq_10",
    100: "pgo_freq_100",
    1000: "pgo_freq_1000",
    10000: "pgo_freq_10000",
    100000: "pgo_freq_100000",
}


def bits_to_kib(entries: int, bits_per_entry: int = DEFAULT_BITS_PER_ENTRY) -> float:
    return entries * bits_per_entry / 8.0 / 1024.0


def count_fct_entries(csv_path: Path) -> tuple[int, int]:
    seen_ld1: set[str] = set()
    rows = 0
    with csv_path.open(newline="") as fh:
        reader = csv.reader(fh)
        if not next(reader, None):
            return 0, 0
        for row in reader:
            if not row or not row[0].strip():
                continue
            rows += 1
            ld1_pc = row[0].strip()
            if ld1_pc not in seen_ld1:
                seen_ld1.add(ld1_pc)
    return rows, len(seen_ld1)


def discover_simpoint_weights(
    workloads_db: Path,
    workloads: list[str],
) -> dict[str, dict[str, float]]:
    data = json.loads(workloads_db.read_text())
    suite = data["datacenter"]["datacenter"]
    weights: dict[str, dict[str, float]] = {}
    for wl in workloads:
        if wl not in suite:
            continue
        weights[wl] = {
            str(sp["cluster_id"]): float(sp["weight"])
            for sp in suite[wl]["simpoints"]
        }
    return weights


def weighted_expected_storage_kib(
    freq_dir: Path,
    weights_by_wl: dict[str, dict[str, float]],
    bits_per_entry: int = DEFAULT_BITS_PER_ENTRY,
) -> tuple[float, float]:
    """Return (expected KiB, expected entries) using simpoint weights."""
    total_kib = 0.0
    total_entries = 0.0
    weight_sum = 0.0
    for wl, sp_weights in weights_by_wl.items():
        wl_dir = freq_dir / wl
        if not wl_dir.is_dir():
            continue
        for cluster_id, weight in sp_weights.items():
            csv_path = wl_dir / f"{cluster_id}.csv"
            if not csv_path.is_file():
                continue
            _rows, entries = count_fct_entries(csv_path)
            total_kib += weight * bits_to_kib(entries, bits_per_entry)
            total_entries += weight * entries
            weight_sum += weight
    if weight_sum == 0.0:
        return float("nan"), float("nan")
    return total_kib / weight_sum, total_entries / weight_sum


def weighted_ipc(
    stats_csv: Path,
    weights_by_wl: dict[str, dict[str, float]],
    config: str,
) -> float:
    import pandas as pd

    df = pd.read_csv(stats_csv, low_memory=False)
    ipc_row = df[df["stats"] == "IPC"].iloc[0]
    total = 0.0
    weight_sum = 0.0
    for wl, sp_weights in weights_by_wl.items():
        for cluster_id, weight in sp_weights.items():
            col = f"{config} {wl} {cluster_id}"
            if col not in df.columns:
                continue
            val = float(ipc_row[col])
            if math.isnan(val):
                continue
            total += val * weight
            weight_sum += weight
    return total / weight_sum if weight_sum else float("nan")


def build_pareto_points(
    *,
    pgo_root: Path,
    stats_csv: Path,
    workloads_db: Path,
    workloads: list[str],
    frequencies: list[int],
    baseline_config: str,
    bits_per_entry: int = DEFAULT_BITS_PER_ENTRY,
    config_names: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    config_names = config_names or FREQ_TO_CONFIG
    weights = discover_simpoint_weights(workloads_db, workloads)
    baseline_ipc = weighted_ipc(stats_csv, weights, baseline_config)
    points: list[dict[str, Any]] = []

    for freq in frequencies:
        config = config_names.get(freq, f"pgo_freq_{freq}")
        freq_dir = pgo_root / f"pgo-candidates-frequency-{freq}"
        if not freq_dir.is_dir():
            continue
        storage_kib, expected_entries = weighted_expected_storage_kib(
            freq_dir, weights, bits_per_entry
        )
        ipc = weighted_ipc(stats_csv, weights, config)
        speedup = ipc / baseline_ipc if baseline_ipc and not math.isnan(ipc) else float("nan")
        speedup_pct = (speedup - 1.0) * 100.0 if not math.isnan(speedup) else float("nan")
        points.append(
            {
                "frequency": freq,
                "config": config,
                "weighted_storage_kib": storage_kib,
                "weighted_fct_entries": expected_entries,
                "weighted_ipc": ipc,
                "speedup_vs_baseline": speedup,
                "speedup_pct": speedup_pct,
            }
        )

    points.sort(key=lambda row: row["weighted_storage_kib"])
    prev_storage = None
    prev_speedup_pct = None
    for row in points:
        if prev_storage is None or prev_speedup_pct is None:
            row["marginal_speedup_pct_per_kib"] = float("nan")
        else:
            delta_storage = row["weighted_storage_kib"] - prev_storage
            delta_speedup = row["speedup_pct"] - prev_speedup_pct
            row["marginal_speedup_pct_per_kib"] = (
                delta_speedup / delta_storage if delta_storage > 0 else float("nan")
            )
        prev_storage = row["weighted_storage_kib"]
        prev_speedup_pct = row["speedup_pct"]
    return points


def write_pareto_csv(path: Path, points: list[dict[str, Any]]) -> None:
    if not points:
        return
    fieldnames = list(points[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(points)


def plot_pareto(
    points: list[dict[str, Any]],
    output_path: Path,
    *,
    title: str,
    x_label: str,
    y_label: str,
) -> None:
    import matplotlib.pyplot as plt

    xs = [row["weighted_storage_kib"] for row in points]
    ys = [row["speedup_pct"] for row in points]
    labels = [str(row["frequency"]) for row in points]

    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    ax.plot(xs, ys, marker="o", linewidth=1.8, markersize=7, color="#1f77b4")
    for x, y, label in zip(xs, ys, labels):
        ax.annotate(
            f"N={label}",
            (x, y),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=9,
        )
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, linestyle="--", alpha=0.35)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=160)
    plt.close(fig)


def print_pareto_table(points: list[dict[str, Any]], baseline_config: str) -> None:
    print(f"\nPareto points (simpoint-weighted, baseline={baseline_config}):")
    print(
        f"{'freq':>8}  {'storage_kib':>12}  {'entries':>10}  "
        f"{'ipc':>10}  {'speedup%':>10}  {'marg%/KiB':>10}"
    )
    for row in points:
        print(
            f"{row['frequency']:>8}  {row['weighted_storage_kib']:>12.3f}  "
            f"{row['weighted_fct_entries']:>10.1f}  "
            f"{row['weighted_ipc']:>10.5f}  {row['speedup_pct']:>10.2f}  "
            f"{row.get('marginal_speedup_pct_per_kib', float('nan')):>10.3f}"
        )


def run_from_descriptor(
    descriptor: dict[str, Any],
    stats_csv: Path,
    output_dir: Path,
    pareto_entry: dict[str, Any],
    *,
    baseline_config: str,
    plot_configs: list[str],
) -> int:
    configured_root = pareto_entry.get("pgo_root")
    if configured_root:
        pgo_root = Path(str(configured_root))
    else:
        root_dir = descriptor.get("root_dir")
        if root_dir:
            pgo_root = Path(str(root_dir)) / "pgo-candidates"
        else:
            scarab_path = descriptor.get("scarab_path") or (REPO_ROOT.parent / "scarab")
            pgo_root = Path(str(scarab_path)) / "src" / "pgo-candidates"

    workloads_db = Path(
        pareto_entry.get("workloads_db") or DEFAULT_WORKLOADS_DB
    )
    workloads = list(pareto_entry.get("workloads") or DEFAULT_PGO_WORKLOADS)
    frequencies = list(pareto_entry.get("frequencies") or DEFAULT_FREQUENCIES)
    bits_per_entry = int(pareto_entry.get("bits_per_entry") or DEFAULT_BITS_PER_ENTRY)

    requested_configs = {
        FREQ_TO_CONFIG.get(freq, f"pgo_freq_{freq}") for freq in frequencies
    }
    missing_configs = sorted(requested_configs - set(plot_configs))
    if missing_configs:
        print(
            "WARN: Pareto configs missing from collected stats (skipping those points): "
            + ", ".join(missing_configs)
        )

    points = build_pareto_points(
        pgo_root=pgo_root,
        stats_csv=stats_csv,
        workloads_db=workloads_db,
        workloads=workloads,
        frequencies=frequencies,
        baseline_config=baseline_config,
        bits_per_entry=bits_per_entry,
    )
    points = [p for p in points if p["config"] in plot_configs]
    if not points:
        print("No Pareto points could be built (missing stats or PGO files).")
        return 1

    stem = str(pareto_entry.get("name") or "fct_storage_ipc_pareto")
    csv_path = output_dir / f"{stem}.csv"
    png_path = output_dir / f"{stem}.png"
    write_pareto_csv(csv_path, points)
    plot_pareto(
        points,
        png_path,
        title=str(
            pareto_entry.get("title")
            or "PGO FCT storage vs IPC (simpoint-weighted)"
        ),
        x_label=str(
            pareto_entry.get("x_label")
            or "Expected FCT storage (KiB, simpoint-weighted)"
        ),
        y_label=str(
            pareto_entry.get("y_label") or "Weighted IPC speedup vs baseline (%)"
        ),
    )
    print_pareto_table(points, baseline_config)
    print(f"Wrote Pareto CSV → {csv_path}")
    print(f"Wrote Pareto plot → {png_path}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True, help="Experiment JSON path.")
    parser.add_argument("--stats-csv", required=True, help="collected_stats.csv path.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for pareto.csv/png (defaults to experiment simulations dir).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    descriptor_path = Path(args.descriptor)
    descriptor = json.loads(descriptor_path.read_text())
    stats_csv = Path(args.stats_csv)
    experiment = descriptor.get("experiment", "experiment")
    root_dir = Path(descriptor.get("root_dir", REPO_ROOT))
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else root_dir / "simulations" / experiment
    )

    visualize = descriptor.get("visualize") or {}
    pareto_entry = None
    for entry in visualize.get("counters") or []:
        if isinstance(entry, dict) and entry.get("type") == "pareto":
            pareto_entry = entry
            break
    if pareto_entry is None:
        pareto_entry = descriptor.get("pareto") or {}
        if not pareto_entry:
            print("No pareto entry found in descriptor visualize.counters or pareto.")
            return 1

    baseline = str(visualize.get("baseline") or pareto_entry.get("baseline") or "baseline")
    configs = list(visualize.get("configs") or [])
    return run_from_descriptor(
        descriptor,
        stats_csv,
        output_dir,
        pareto_entry,
        baseline_config=baseline,
        plot_configs=configs or list(FREQ_TO_CONFIG.values()),
    )


if __name__ == "__main__":
    raise SystemExit(main())
