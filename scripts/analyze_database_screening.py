#!/usr/bin/env python3
"""Aggregate native database candidate screens and select stable winners."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path


PERF_EVENTS = {
    "cycles": "cycles",
    "instructions": "instructions",
    "stalled-cycles-backend": "backend_stalls",
    "cache-misses": "cache_misses",
    "branches": "branches",
    "branch-misses": "branch_misses",
}


def parse_number(value: str) -> float | None:
    value = value.strip().replace(" ", "")
    if not value or value.startswith("<"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_perf(path: Path) -> dict[str, float | None]:
    counters: dict[str, float | None] = {value: None for value in PERF_EVENTS.values()}
    with path.open(newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 3:
                continue
            event = row[2].strip().split(":", 1)[0]
            target = PERF_EVENTS.get(event)
            if target is not None:
                counters[target] = parse_number(row[0])
    return counters


def parse_throughput(run_dir: Path, system: str) -> float | None:
    if system in ("mongodb_aggregate", "mysql_tpch"):
        duration_path = run_dir / "duration-seconds.txt"
        if duration_path.is_file():
            duration = float(duration_path.read_text().strip())
            return 1.0 / duration if duration > 0 else None
    if system == "mysql":
        summaries = sorted((run_dir / "benchbase-results").glob("*.summary.json"))
        if summaries:
            summary = json.loads(summaries[-1].read_text())
            value = summary.get("Throughput (requests/second)")
            if value is not None:
                return float(value)

    path = run_dir / "workload.log"
    text = path.read_text(errors="replace")
    patterns = (
        [r"\[OVERALL\],\s*Throughput\(ops/sec\),\s*([0-9.]+)"]
        if system in ("mongodb", "mongodb_aggregate")
        else [
            r"([0-9]+(?:\.[0-9]+)?)\s*requests/sec\s*\(throughput\)",
            r"([0-9]+(?:\.[0-9]+)?)\s*requests/sec",
        ]
    )
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            return float(matches[-1])
    return None


def parse_errors(path: Path, system: str) -> int:
    text = path.read_text(errors="replace")
    if system in ("mongodb", "mongodb_aggregate"):
        values = re.findall(
            r"\[[A-Z-]+-FAILED\],\s*Operations,\s*([0-9]+)", text
        )
        return sum(int(value) for value in values)
    values = re.findall(r"(?:Errors|Failed Transactions)[^0-9]*([0-9]+)", text, re.IGNORECASE)
    return max((int(value) for value in values), default=0)


def parse_cpu(path: Path) -> float | None:
    values: list[float] = []
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] != "Average:":
            continue
        if not fields[1].isdigit() or not 0 <= int(fields[1]) <= 23:
            continue
        try:
            values.append(100.0 - float(fields[-1]))
        except ValueError:
            continue
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
        perf = parse_perf(run_dir / "perf.csv")
        throughput = parse_throughput(run_dir, manifest["system"])
        errors = parse_errors(run_dir / "workload.log", manifest["system"])
        row: dict[str, object] = {
            "workload": manifest["workload"],
            "system": manifest["system"],
            "repetition": int(manifest["repetition"]),
            "throughput": throughput,
            "errors": errors,
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
    result: list[dict[str, object]] = []
    workloads = sorted({str(row["workload"]) for row in rows})
    for workload in workloads:
        group = [row for row in rows if row["workload"] == workload]
        throughputs = [float(row["throughput"]) for row in group if row["throughput"] is not None]
        stalls = [float(row["backend_stall_pct"]) for row in group if row["backend_stall_pct"] is not None]
        def coefficient_of_variation(values: list[float]) -> float:
            mean = statistics.fmean(values) if values else math.nan
            return (
                statistics.pstdev(values) / mean * 100.0
                if len(values) >= 2 and mean > 0
                else math.nan
            )

        mean_throughput = statistics.fmean(throughputs) if throughputs else math.nan
        throughput_cv = coefficient_of_variation(throughputs)
        instructions = [float(row["instructions"]) for row in group if row["instructions"] is not None]
        cycles = [float(row["cycles"]) for row in group if row["cycles"] is not None]
        instruction_cv = coefficient_of_variation(instructions)
        cycle_cv = coefficient_of_variation(cycles)
        eligible = (
            len(group) == 2
            and len(throughputs) == 2
            and len(stalls) == 2
            and sum(int(row["errors"]) for row in group) == 0
            and throughput_cv <= 5.0
            and instruction_cv <= 5.0
            and cycle_cv <= 5.0
        )
        cpu_values = [
            float(row["cpu_util_pct"])
            for row in group
            if row["cpu_util_pct"] is not None
        ]
        result.append(
            {
                "workload": workload,
                "system": group[0]["system"],
                "runs": len(group),
                "mean_throughput": mean_throughput,
                "throughput_cv_pct": throughput_cv,
                "instruction_cv_pct": instruction_cv,
                "cycle_cv_pct": cycle_cv,
                "median_backend_stall_pct": statistics.median(stalls) if stalls else math.nan,
                "mean_cpu_util_pct": statistics.fmean(cpu_values) if cpu_values else math.nan,
                "errors": sum(int(row["errors"]) for row in group),
                "eligible": eligible,
            }
        )
    return result


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
    parser.add_argument(
        "--repetitions",
        help="Comma-separated repetitions to include, for example 1,2.",
    )
    args = parser.parse_args()

    rows = load_runs(args.root)
    if args.repetitions:
        repetitions = {int(value) for value in args.repetitions.split(",")}
        rows = [row for row in rows if int(row["repetition"]) in repetitions]
    if not rows:
        raise SystemExit(f"No manifest.json files found under {args.root}")
    summaries = summarize(rows)
    write_csv(args.out_dir / "candidate_runs.csv", rows)
    write_csv(args.out_dir / "candidate_summary.csv", summaries)

    winners: dict[str, dict[str, object]] = {}
    for system in ("mysql", "mongodb"):
        eligible = [
            row for row in summaries
            if (
                row["system"] == system
                or (system == "mongodb" and row["system"] == "mongodb_aggregate")
                or (system == "mysql" and row["system"] == "mysql_tpch")
            )
            and row["eligible"]
        ]
        if eligible:
            winners[system] = max(eligible, key=lambda row: float(row["median_backend_stall_pct"]))
    (args.out_dir / "selected_candidates.json").write_text(json.dumps(winners, indent=2) + "\n")
    print(json.dumps(winners, indent=2))


if __name__ == "__main__":
    main()
