#!/usr/bin/env python3
"""Summarize PGO FCT storage overhead vs IPC for frequency-threshold sweeps.

Hardware FCT entry model (paper table): 108 bits/entry
  LD1 tag 41 + LD2 PC 48 + signed offset 7 + size 3 + confidence 9

FCT preload keeps one row per unique LD1 PC (first CSV row wins), matching
ifuse_fct.c. This script counts unique LD1 PCs per simpoint CSV and aggregates
storage across workloads. Optionally merges weighted IPC from a scarab-infra
collected_stats.csv produced by the pgo_ifuse_frequency_sweep experiment.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

BITS_PER_FCT_ENTRY = 108
DEFAULT_FREQUENCIES = [1, 10, 100, 1000, 10000, 100000]
PGO_WORKLOADS = [
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


def bits_to_kib(entries: int) -> float:
    return entries * BITS_PER_FCT_ENTRY / 8.0 / 1024.0


def count_fct_entries(csv_path: Path) -> tuple[int, int]:
    """Return (csv_data_rows, unique_ld1_entries)."""
    seen_ld1: set[str] = set()
    rows = 0
    with csv_path.open(newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return 0, 0
        for row in reader:
            if not row or not row[0].strip():
                continue
            rows += 1
            ld1_pc = row[0].strip()
            if ld1_pc not in seen_ld1:
                seen_ld1.add(ld1_pc)
    return rows, len(seen_ld1)


def discover_simpoint_weights(workloads_db: Path) -> dict[str, dict[str, float]]:
    data = json.loads(workloads_db.read_text())
    suite = data["datacenter"]["datacenter"]
    weights: dict[str, dict[str, float]] = {}
    for wl in PGO_WORKLOADS:
        if wl not in suite:
            continue
        weights[wl] = {
            str(sp["cluster_id"]): float(sp["weight"])
            for sp in suite[wl]["simpoints"]
        }
    return weights


def summarize_storage(pgo_root: Path, frequencies: list[int]) -> list[dict]:
    rows: list[dict] = []
    for freq in frequencies:
        freq_dir = pgo_root / f"pgo-candidates-frequency-{freq}"
        if not freq_dir.is_dir():
            print(f"warning: missing directory {freq_dir}", file=sys.stderr)
            continue

        total_csv_rows = 0
        total_fct_entries = 0
        worst_workload = ""
        worst_simpoint = ""
        worst_entries = 0
        per_workload_peak: dict[str, int] = defaultdict(int)

        for wl in sorted(PGO_WORKLOADS):
            wl_dir = freq_dir / wl
            if not wl_dir.is_dir():
                continue
            for name in sorted(os.listdir(wl_dir)):
                if not name.endswith(".csv"):
                    continue
                csv_rows, fct_entries = count_fct_entries(wl_dir / name)
                total_csv_rows += csv_rows
                total_fct_entries += fct_entries
                per_workload_peak[wl] = max(per_workload_peak[wl], fct_entries)
                if fct_entries > worst_entries:
                    worst_entries = fct_entries
                    worst_workload = wl
                    worst_simpoint = name[:-4]

        rows.append(
            {
                "frequency": freq,
                "config": FREQ_TO_CONFIG.get(freq, f"pgo_freq_{freq}"),
                "total_csv_rows": total_csv_rows,
                "total_fct_entries": total_fct_entries,
                "total_storage_kib": bits_to_kib(total_fct_entries),
                "worst_simpoint": f"{worst_workload}/{worst_simpoint}",
                "worst_simpoint_entries": worst_entries,
                "worst_simpoint_storage_kib": bits_to_kib(worst_entries),
                "worst_workload_peak_entries": max(per_workload_peak.values(), default=0),
                "worst_workload_peak_storage_kib": bits_to_kib(
                    max(per_workload_peak.values(), default=0)
                ),
            }
        )
    return rows


def load_ipc_by_config(
    stats_csv: Path, weights: dict[str, dict[str, float]], baseline_config: str
) -> dict[str, dict[str, float]]:
    """Return per-config weighted IPC and speedup vs baseline."""
    import pandas as pd

    df = pd.read_csv(stats_csv, low_memory=False)
    ipc_row = df[df["stats"] == "IPC"].iloc[0]
    weight_row = df[df["stats"] == "Weight"].iloc[0]

    def weighted_ipc(config: str) -> float:
        total = 0.0
        weight_sum = 0.0
        for wl, wl_weights in weights.items():
            for cluster_id, w in wl_weights.items():
                col = f"{config} {wl} {cluster_id}"
                if col not in df.columns:
                    continue
                val = float(ipc_row[col])
                if math.isnan(val):
                    continue
                total += val * w
                weight_sum += w
        return total / weight_sum if weight_sum else float("nan")

    baseline_ipc = weighted_ipc(baseline_config)
    out: dict[str, dict[str, float]] = {}
    configs = {
        str(v).strip()
        for v in df[df["stats"] == "Configuration"].iloc[0][3:].tolist()
        if str(v).strip()
    }
    for config in sorted(configs):
        ipc = weighted_ipc(config)
        speedup = ipc / baseline_ipc if baseline_ipc and not math.isnan(ipc) else float("nan")
        out[config] = {"ipc": ipc, "speedup_vs_baseline": speedup}
    return out


def write_summary_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize PGO FCT storage overhead for frequency thresholds."
    )
    parser.add_argument(
        "--pgo-root",
        default="/users/deepmish/scarab/src/pgo-candidates",
        help="Root directory containing pgo-candidates-frequency-<N>/ trees.",
    )
    parser.add_argument(
        "--frequencies",
        nargs="*",
        type=int,
        default=DEFAULT_FREQUENCIES,
        help="Occurrence thresholds to summarize.",
    )
    parser.add_argument(
        "--workloads-db",
        default="/users/deepmish/scarab-infra/workloads/workloads_db.json",
        help="workloads_db.json for simpoint weights.",
    )
    parser.add_argument(
        "--stats-csv",
        default=None,
        help="Optional collected_stats.csv from pgo_ifuse_frequency_sweep.",
    )
    parser.add_argument(
        "--baseline-config",
        default="baseline",
        help="Baseline configuration name inside --stats-csv.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write storage summary CSV to this path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pgo_root = Path(args.pgo_root)
    rows = summarize_storage(pgo_root, args.frequencies)

    if args.stats_csv:
        weights = discover_simpoint_weights(Path(args.workloads_db))
        ipc_by_config = load_ipc_by_config(
            Path(args.stats_csv), weights, args.baseline_config
        )
        for row in rows:
            cfg = row["config"]
            if cfg in ipc_by_config:
                row["weighted_ipc"] = ipc_by_config[cfg]["ipc"]
                row["speedup_vs_baseline"] = ipc_by_config[cfg]["speedup_vs_baseline"]
        baseline_ipc = ipc_by_config.get(args.baseline_config, {}).get("ipc")
        if baseline_ipc is not None:
            print(f"baseline IPC ({args.baseline_config}): {baseline_ipc:.6f}")

    print(
        f"{'freq':>8}  {'fct_entries':>12}  {'storage_kib':>12}  "
        f"{'worst_sp_entries':>16}  {'worst_sp_kib':>12}"
    )
    for row in rows:
        ipc_note = ""
        if "speedup_vs_baseline" in row and not math.isnan(row["speedup_vs_baseline"]):
            ipc_note = f"  speedup={row['speedup_vs_baseline']:.4f}"
        print(
            f"{row['frequency']:>8}  {row['total_fct_entries']:>12}  "
            f"{row['total_storage_kib']:>12.2f}  "
            f"{row['worst_simpoint_entries']:>16}  "
            f"{row['worst_simpoint_storage_kib']:>12.2f}"
            f"{ipc_note}"
        )
        print(f"           worst simpoint: {row['worst_simpoint']}")

    if args.output:
        write_summary_csv(Path(args.output), rows)
        print(f"\nWrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
