#!/usr/bin/env python3
"""Validate and rank native PostgreSQL TPC-H query screens."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


EVENTS = {
    "cycles": "cycles",
    "instructions": "instructions",
    "stalled-cycles-backend": "backend_stalls",
    "cache-misses": "cache_misses",
    "branches": "branches",
    "branch-misses": "branch_misses",
}


def parse_perf(path: Path) -> dict[str, float | None]:
    counters = {target: None for target in EVENTS.values()}
    with path.open(newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 3:
                continue
            target = EVENTS.get(row[2].strip().split(":", 1)[0])
            if target is None or row[0].strip().startswith("<"):
                continue
            try:
                counters[target] = float(row[0].strip())
            except ValueError:
                pass
    return counters


def parse_cpu(path: Path) -> float | None:
    values: list[float] = []
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] != "Average:" or not fields[1].isdigit():
            continue
        if 0 <= int(fields[1]) <= 23:
            values.append(100.0 - float(fields[-1]))
    return statistics.fmean(values) if values else None


def ratio(numerator: float | None, denominator: float | None, scale: float = 1.0) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * scale


def load_runs(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for manifest_path in sorted(root.rglob("manifest.json")):
        run_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("system") != "postgresql":
            continue
        perf = parse_perf(run_dir / "perf.csv")
        duration = float((run_dir / "duration-seconds.txt").read_text().strip())
        status = int((run_dir / "exit-status.txt").read_text().strip())
        row: dict[str, object] = {
            **manifest,
            "duration_seconds": duration,
            "exit_status": status,
            "cpu_util_pct": parse_cpu(run_dir / "mpstat.log"),
            **perf,
        }
        row["ipc"] = ratio(perf["instructions"], perf["cycles"])
        row["backend_stall_pct"] = ratio(perf["backend_stalls"], perf["cycles"], 100.0)
        row["cache_miss_mpki"] = ratio(perf["cache_misses"], perf["instructions"], 1000.0)
        row["branch_mpki"] = ratio(perf["branch_misses"], perf["instructions"], 1000.0)
        rows.append(row)
    return rows


def summarize(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for workload in sorted({str(row["workload"]) for row in rows}):
        group = [row for row in rows if row["workload"] == workload]
        durations = [float(row["duration_seconds"]) for row in group]
        stalls = [float(row["backend_stall_pct"]) for row in group if row["backend_stall_pct"] is not None]
        mean_duration = statistics.fmean(durations)
        def coefficient_of_variation(values: list[float]) -> float:
            mean = statistics.fmean(values)
            return (
                statistics.pstdev(values) / mean * 100.0
                if len(values) >= 2 and mean > 0
                else math.nan
            )

        duration_cv = coefficient_of_variation(durations)
        instructions = [
            float(row["instructions"]) for row in group if row["instructions"] is not None
        ]
        cycles = [float(row["cycles"]) for row in group if row["cycles"] is not None]
        instruction_cv = coefficient_of_variation(instructions)
        cycle_cv = coefficient_of_variation(cycles)
        complete_counters = all(
            row[name] is not None for row in group for name in EVENTS.values()
        )
        eligible = (
            len(group) == 2
            and all(int(row["exit_status"]) == 0 for row in group)
            and len(stalls) == 2
            and complete_counters
            and duration_cv <= 5.0
            and instruction_cv <= 5.0
            and cycle_cv <= 5.0
        )
        cpu = [float(row["cpu_util_pct"]) for row in group if row["cpu_util_pct"] is not None]
        summaries.append({
            "workload": workload,
            "scale_factor": group[0]["scale_factor"],
            "query": group[0]["query"],
            "runs": len(group),
            "mean_duration_seconds": mean_duration,
            "duration_cv_pct": duration_cv,
            "instruction_cv_pct": instruction_cv,
            "cycle_cv_pct": cycle_cv,
            "median_backend_stall_pct": statistics.median(stalls) if stalls else math.nan,
            "mean_cpu_util_pct": statistics.fmean(cpu) if cpu else math.nan,
            "eligible": eligible,
        })
    return summaries


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--scale-factor", type=float)
    args = parser.parse_args()
    rows = load_runs(args.root)
    if args.scale_factor is not None:
        rows = [
            row for row in rows
            if math.isclose(float(row["scale_factor"]), args.scale_factor)
        ]
    if not rows:
        suffix = f" for SF{args.scale_factor:g}" if args.scale_factor is not None else ""
        raise SystemExit(f"No PostgreSQL manifests found under {args.root}{suffix}")
    summaries = summarize(rows)
    write_csv(args.out_dir / "query_runs.csv", rows)
    write_csv(args.out_dir / "query_summary.csv", summaries)
    eligible = [row for row in summaries if row["eligible"]]
    selected = max(eligible, key=lambda row: float(row["median_backend_stall_pct"])) if eligible else {}
    (args.out_dir / "selected_query.json").write_text(json.dumps(selected, indent=2) + "\n")
    print(json.dumps(selected, indent=2))


if __name__ == "__main__":
    main()
