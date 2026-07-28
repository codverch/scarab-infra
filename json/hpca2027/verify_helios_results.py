#!/usr/bin/env python3
"""Verify Helios sims, mcpat outputs, IPC speedup; tune knobs and write results README."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HELIOS_APPS = [
    "appworld", "bfs-web-google", "bfs-init", "clickhouse", "corebench",
    "dfs-web-google", "dfs-init", "duckdb", "grpc", "leveldb", "memcached",
    "pagerank-gnutella31", "pagerank-init", "rocksdb", "sqlite",
    "sssp-ego-facebook", "sssp-init", "terminal_bench",
]

# workload_db key -> simulations/ dir name (underscores)
SIM_DIR = {
    "bfs-web-google": "bfs-web-google",
    "dfs-web-google": "dfs-web-google",
    "corebench": "corebench",
    "pagerank-gnutella31": "pagerank-gnutella31",
    "sssp-ego-facebook": "sssp-ego-facebook",
}


def sim_dir_name(app: str) -> str:
    return SIM_DIR.get(app, app)


def parse_ipc_from_core_stat(path: Path) -> float | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.startswith("NODE_INST_COUNT") and "total" not in line.lower():
            parts = line.split()
            if len(parts) >= 2:
                inst = float(parts[-1])
        if line.startswith("NODE_CYCLE") and "total" not in line.lower():
            parts = line.split()
            if len(parts) >= 2:
                cyc = float(parts[-1])
    try:
        return inst / cyc if cyc else None
    except NameError:
        return None


def weighted_ipc_from_csv(csv_path: Path) -> float | None:
    if not csv_path.exists():
        return None
    total_inst = total_cyc = 0.0
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            stat = row.get("Stat", "")
            if stat == "NODE_INST_COUNT_total_count":
                total_inst += float(row.get("Value", 0) or 0)
            if stat == "NODE_CYCLE_total_count":
                total_cyc += float(row.get("Value", 0) or 0)
    return total_inst / total_cyc if total_cyc else None


def collect_simpoint_ipcs(sim_root: Path, app: str) -> list[tuple[str, float]]:
    d = sim_root / sim_dir_name(app)
    if not d.is_dir():
        return []
    out = []
    for sp in sorted(d.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else p.name):
        if not sp.is_dir():
            continue
        ipc = parse_ipc_from_core_stat(sp / "core.stat.0.out")
        if ipc is not None:
            out.append((sp.name, ipc))
    return out


def app_weighted_ipc(sim_root: Path, app: str) -> float | None:
    points = collect_simpoint_ipcs(sim_root, app)
    if not points:
        return None
    return sum(ipc for _, ipc in points) / len(points)


def count_mcpat(sim_root: Path, app: str) -> tuple[int, int]:
    """Return (simpoints_with_mcpat, total_simpoints)."""
    d = sim_root / sim_dir_name(app)
    ok = total = 0
    if not d.is_dir():
        return 0, 0
    for sp in d.iterdir():
        if not sp.is_dir():
            continue
        total += 1
        if (sp / "mcpat.out").is_file() and (sp / "power_model_results.out").is_file():
            ok += 1
    return ok, total


def load_helios_knobs(helios_sh: Path) -> dict[str, str]:
    text = helios_sh.read_text()
    apps = {}
    for app in HELIOS_APPS:
        m = re.search(rf'\[{re.escape(app)}\]=(\d+)', text)
        if m:
            apps[app] = m.group(1)
    return apps


def write_readme(results_dir: Path, rows: list[dict], knobs: dict[str, str], mcpat_summary: dict):
    lines = [
        "# HPCA 2027 Helios Final Results",
        "",
        "## Simulation window",
        "- warmup: 20,000,000",
        "- inst_limit: 30,000,000 (10M measured after warmup)",
        "- architecture: `in` (PARAMS.in) for Helios; baseline uses `golden_cove`",
        "- stores: off (`--helios_fuse_stores 0`)",
        "",
        "## Per-app Helios predictor config (T=threshold, W=64, I=increment, D=decrement)",
        "",
        "| App | T | Config label |",
        "|-----|---|--------------|",
    ]
    for app in HELIOS_APPS:
        t = knobs.get(app, "?")
        lines.append(f"| {app} | {t} | T{t}/W64/I1/D10/stores-off |")
    lines += [
        "",
        "## IPC speedup (Helios / Baseline, simpoint-average)",
        "",
        "| App | Baseline IPC | Helios IPC | Speedup | mcpat ok |",
        "|-----|--------------|------------|---------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['app']} | {r['baseline_ipc']:.4f} | {r['helios_ipc']:.4f} | "
            f"{r['speedup']:.4f} | {r['mcpat_ok']}/{r['mcpat_total']} |"
        )
    lines += [
        "",
        "## Power modeling",
        "- `--power_intf_on 1` on all runs",
        f"- McPAT: `{Path('/users/deepmish/mcpat/mcpat').resolve()}` (includes `helios.cc` structures)",
        "- Scarab emits `system.core0.helios` in `mcpat_infile.xml` with FP/UCH/head-table params + access stats",
        "",
        "## mcpat summary",
        json.dumps(mcpat_summary, indent=2),
    ]
    (results_dir / "README.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--infra-dir", type=Path, required=True)
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--scarab-root", type=Path, default=Path("/users/deepmish/scarab/src"))
    args = ap.parse_args()

    baseline_root = args.scarab_root / "simulations" / "baseline"
    helios_root = args.scarab_root / "simulations" / "helios"
    args.results_dir.mkdir(parents=True, exist_ok=True)

    knobs = load_helios_knobs(args.infra_dir / "json/hpca2027/helios.sh")
    rows = []
    mcpat_summary = {}
    negative = []

    for app in HELIOS_APPS:
        b_ipc = app_weighted_ipc(baseline_root, app)
        h_ipc = app_weighted_ipc(helios_root, app)
        m_ok, m_tot = count_mcpat(helios_root, app)
        mcpat_summary[app] = {"ok": m_ok, "total": m_tot}
        if b_ipc is None or h_ipc is None:
            rows.append({
                "app": app, "baseline_ipc": b_ipc or 0, "helios_ipc": h_ipc or 0,
                "speedup": 0, "mcpat_ok": m_ok, "mcpat_total": m_tot, "missing": True,
            })
            negative.append(app)
            continue
        speedup = h_ipc / b_ipc
        rows.append({
            "app": app, "baseline_ipc": b_ipc, "helios_ipc": h_ipc,
            "speedup": speedup, "mcpat_ok": m_ok, "mcpat_total": m_tot, "missing": False,
        })
        if speedup < 1.0:
            negative.append(app)

    write_readme(args.results_dir, rows, knobs, mcpat_summary)

    with (args.results_dir / "ipc_summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # Copy key artifacts
    for exp in ("baseline", "helios"):
        src = args.scarab_root / "simulations" / exp
        if src.is_dir():
            dst = args.results_dir / "simulations" / exp
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.warmup"))

    if negative:
        print("APPS_NEEDING_ATTENTION:", " ".join(negative), file=sys.stderr)
        return 1
    print("All apps show positive speedup; mcpat checks in README.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
