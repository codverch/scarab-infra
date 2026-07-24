#!/usr/bin/env python3
"""Summarize rfp-prob-shift-sweep vs default rfp_24kb (prob_shift=4)."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent.parent / "hpca2027-main-graphs"
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

WORKLOADS = [
    "appworld",
    "bfs",
    "clickhouse",
    "core_bench",
    "dfs",
    "duckdb",
    "pagerank",
    "rocksdb",
    "terminal_bench",
]

from plot_ipc import (  # noqa: E402
    DEFAULT_TRACE_ROOT,
    find_simpoint_dir,
    ipc_from_sim_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

ROOT = Path("/users/deepmish/scarab/src")
SIM_ROOT = ROOT / "simulations"
LEGACY_ROOT = ROOT / "simulations-confidence-1"
CONFIGS = [
    ("rfp_24kb (p=1/16)", LEGACY_ROOT, "rfp_24kb"),
    ("rfp_prob_p2 (p=1/4)", SIM_ROOT, "rfp_prob_p2"),
    ("rfp_prob_p0 (p=1/1)", SIM_ROOT, "rfp_prob_p0"),
]
BASELINE = LEGACY_ROOT / "baseline"


def stat_val(path: Path, name: str) -> float | None:
    if not path.is_file():
        return None
    with path.open(newline="") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) >= 3 and row[0].strip() == name:
                try:
                    return float(row[2].strip())
                except ValueError:
                    return None
    return None


def ipc(sim_dir: Path) -> float | None:
    return ipc_from_sim_dir(sim_dir)


def rfp_metrics(rfp_csv: Path) -> dict[str, float] | None:
    loads = stat_val(rfp_csv, "RFP_ALL_LOADS_count")
    if not loads or loads <= 0:
        return None
    keys = [
        "RFP_PREDICTION_MADE_count",
        "RFP_PREFETCH_INJECTED_count",
        "RFP_PREFETCH_EXECUTED_count",
        "RFP_PREFETCH_USEFUL_count",
        "RFP_RETIRE_COVERED_count",
    ]
    vals = {k: stat_val(rfp_csv, k) or 0.0 for k in keys}
    return {
        "pred": 100.0 * vals["RFP_PREDICTION_MADE_count"] / loads,
        "inj": 100.0 * vals["RFP_PREFETCH_INJECTED_count"] / loads,
        "exe": 100.0 * vals["RFP_PREFETCH_EXECUTED_count"] / loads,
        "use": 100.0 * vals["RFP_PREFETCH_USEFUL_count"] / loads,
        "cov": 100.0 * vals["RFP_RETIRE_COVERED_count"] / loads,
    }


def weighted_app_metric(
    sim_root: Path,
    config: str,
    workload: str,
    sp_weights: dict,
    metric_fn,
) -> float | None:
    total = 0.0
    wsum = 0.0
    for (wl, cid), w in sp_weights.items():
        if wl != workload or w <= 0:
            continue
        sim = find_simpoint_dir(
            sim_root, config, wl, cid, suite="datacenter", subsuite="datacenter"
        )
        if sim is None:
            continue
        v = metric_fn(sim)
        if v is None:
            continue
        total += w * v
        wsum += w
    if wsum <= 0:
        return None
    return total / wsum


def geomean(vals: list[float]) -> float:
    import math

    xs = [v for v in vals if v and v > 0]
    if not xs:
        return 1.0
    return math.exp(sum(math.log(v) for v in xs) / len(xs))


def main() -> None:
    sp_weights = load_simpoint_trace_weights(DEFAULT_TRACE_ROOT, WORKLOADS)

    print("=== IPC speedup vs baseline (simpoint-weighted geomean per app) ===\n")
    header = f"{'App':16s}" + "".join(f"{label:>18s}" for label, _, _ in CONFIGS)
    print(header)
    speedups_by_cfg: dict[str, list[float]] = {label: [] for label, _, _ in CONFIGS}

    for wl in WORKLOADS:
        row = f"{rename_workload(wl):16s}"
        for label, sim_root, cfg in CONFIGS:
            rfp_ipc = weighted_app_metric(
                sim_root, cfg, wl, sp_weights, lambda s: ipc(s)
            )
            base_ipc = weighted_app_metric(
                BASELINE, "baseline", wl, sp_weights, lambda s: ipc(s)
            )
            if rfp_ipc is None or base_ipc is None or base_ipc <= 0:
                row += f"{'n/a':>18s}"
                continue
            sp = rfp_ipc / base_ipc
            speedups_by_cfg[label].append(sp)
            row += f"{(sp - 1) * 100:>17.1f}%"
        print(row)

    print(f"\n{'Overall geomean':16s}", end="")
    for label, _, _ in CONFIGS:
        gm = geomean(speedups_by_cfg[label])
        print(f"{(gm - 1) * 100:>17.1f}%", end="")
    print()

    print("\n=== RFP funnel (% of on-path loads, simpoint-weighted) ===\n")
    for metric, title in [
        ("pred", "Predicted"),
        ("inj", "Injected"),
        ("exe", "Executed"),
        ("use", "Useful"),
        ("cov", "Covered"),
    ]:
        print(f"-- {title} --")
        hdr = f"{'App':16s}" + "".join(f"{label:>18s}" for label, _, _ in CONFIGS)
        print(hdr)
        for wl in WORKLOADS:
            row = f"{rename_workload(wl):16s}"
            for label, sim_root, cfg in CONFIGS:
                v = weighted_app_metric(
                    sim_root,
                    cfg,
                    wl,
                    sp_weights,
                    lambda s, m=metric: (rfp_metrics(s / "rfp.stat.0.csv") or {}).get(m),
                )
                row += f"{v:>17.1f}%" if v is not None else f"{'n/a':>18s}"
            print(row)
        print()


if __name__ == "__main__":
    main()
