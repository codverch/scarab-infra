#!/usr/bin/env python3
"""Copy every Helios paper-config run into scarab/src/hpca2027-revision/helios-paper-config/.

Reads <root_dir>/simulations/<config>/<app>/<cluster>/ for each config and app in the
descriptor and writes:

  <out>/<app>/<config>/   every file from the run dir (all *.stat.0.out/.csv, PARAMS.in,
                          PARAMS.out, sim.log, ...)
  <out>/summary.csv       cycles, instructions, IPC and Helios counters per app x config,
                          plus Helios speedup over no_fusion

Usage: package_helios_paper_results.py --descriptor json/hpca2027-revision/helios_paper.json \
           [--out ~/scarab/src/hpca2027-revision/helios-paper-config]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
from pathlib import Path

HELIOS_COUNTERS = (
    "HELIOS_ONPATH_LOADS",
    "HELIOS_ONPATH_STORES",
    "HELIOS_FUSIONS_COMMITTED",
    "HELIOS_FUSION_MISPREDICT",
    "HELIOS_FLUSHES",
)


def read_ipc(run: Path) -> tuple[int, int, float]:
    text = (run / "core.stat.0.out").read_text()
    m = re.search(r"Cumulative:\s+Cycles:\s+(\d+)\s+Instructions:\s+(\d+)\s+IPC:\s+([\d.]+)", text)
    if not m:
        raise ValueError(f"no Cumulative line in {run / 'core.stat.0.out'}")
    return int(m[1]), int(m[2]), float(m[3])


def read_counters(run: Path) -> dict[str, int]:
    stats = {}
    for f in run.glob("*.stat.0.csv"):
        for line in f.read_text().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[0].endswith("_total_count"):
                try:
                    stats[parts[0][: -len("_total_count")]] = int(parts[2])
                except ValueError:
                    pass
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--descriptor", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    desc = json.loads(args.descriptor.read_text())
    out = args.out or Path(desc["scarab_path"]) / "src/hpca2027-revision/helios-paper-config"
    sims_root = Path(desc["root_dir"]) / "simulations"
    configs = list(desc["configurations"])
    apps = [a for sim in desc["simulations"] for a in sim["workload"]]

    rows, missing = [], []
    for app in apps:
        ipc = {}
        for cfg in configs:
            runs = sorted(p for p in (sims_root / cfg / app).glob("*") if p.is_dir())
            if not runs or not (runs[0] / "core.stat.0.out").exists():
                missing.append(f"{cfg}/{app}")
                continue
            run = runs[0]
            dst = out / app / cfg
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(run, dst, symlinks=False)
            cycles, insts, ipc[cfg] = read_ipc(run)
            counters = read_counters(run)
            rows.append({"app": app, "config": cfg, "cycles": cycles, "instructions": insts,
                         "ipc": ipc[cfg], **{c: counters.get(c, "") for c in HELIOS_COUNTERS}})
        if "no_fusion" in ipc and "helios" in ipc:
            for r in rows:
                if r["app"] == app:
                    r["speedup_vs_no_fusion"] = f"{ipc[r['config']] / ipc['no_fusion']:.4f}"

    out.mkdir(parents=True, exist_ok=True)
    fields = ["app", "config", "cycles", "instructions", "ipc", "speedup_vs_no_fusion", *HELIOS_COUNTERS]
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    speedups = [float(r["speedup_vs_no_fusion"]) for r in rows
                if r["config"] == "helios" and r.get("speedup_vs_no_fusion")]
    for r in rows:
        print(f"{r['app']:12s} {r['config']:10s} IPC {r['ipc']:.4f}  {r.get('speedup_vs_no_fusion', '')}")
    if speedups:
        gm = math.exp(sum(map(math.log, speedups)) / len(speedups))
        print(f"Helios geomean speedup over no_fusion ({len(speedups)} apps): {gm:.4f}")
    if missing:
        print(f"Missing or unfinished: {' '.join(missing)}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
