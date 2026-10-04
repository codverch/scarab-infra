#!/usr/bin/env python3
"""Copy every I-Fuse Helios paper-config run into scarab/src/hpca2027-revision/helios-paper-config-ifuse/.

Reads <root_dir>/simulations/<config>/<app>/<cluster>/ for the single config in the
descriptor and writes:

  <out>/<app>/            every file from the run dir (all *.stat.0.out/.csv, PARAMS.in,
                          PARAMS.out, sim.log, ...)
  <out>/summary.csv       cycles, instructions, IPC and I-Fuse counters per app over the
                          measured window, i.e. after the full_warmup stats reset

Usage: package_helios_paper_ifuse_results.py --descriptor json/hpca2027-revision/helios_paper_ifuse.json \
           [--out ~/scarab/src/hpca2027-revision/helios-paper-config-ifuse]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

IFUSE_COUNTERS = (
    "IFUSE_FUSED_LOADS",
    "IFUSE_LOAD1_PREDICTIONS",
    "IFUSE_CORRECT_PREDICTIONS",
    "IFUSE_INCORRECT_PREDICTIONS",
    "IFUSE_MISPREDICTED_LOADS",
    "IFUSE_TRAINING_PAIRS_DISCOVERED",
)


def read_ipc(run: Path) -> tuple[int, int, float]:
    """Cycles, instructions and IPC after warmup (the Periodic line)."""
    text = (run / "core.stat.0.out").read_text()
    m = re.search(r"Periodic:\s+Cycles:\s+(\d+)\s+Instructions:\s+(\d+)\s+IPC:\s+([\d.]+)", text)
    if not m:
        raise ValueError(f"no Periodic line in {run / 'core.stat.0.out'}")
    return int(m[1]), int(m[2]), float(m[3])


def read_counters(run: Path) -> dict[str, int]:
    """Counters after warmup (<stat>_count; <stat>_total_count includes warmup)."""
    stats = {}
    for f in run.glob("*.stat.0.csv"):
        for line in f.read_text().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[0].endswith("_count") and not parts[0].endswith("_total_count"):
                try:
                    stats[parts[0][: -len("_count")]] = int(parts[2])
                except ValueError:
                    pass
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--descriptor", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    desc = json.loads(args.descriptor.read_text())
    out = args.out or Path(desc["scarab_path"]) / "src/hpca2027-revision/helios-paper-config-ifuse"
    sims_root = Path(desc["root_dir"]) / "simulations"
    (cfg,) = desc["configurations"]
    apps = [a for sim in desc["simulations"] for a in sim["workload"]]

    rows, missing = [], []
    for app in apps:
        runs = sorted(p for p in (sims_root / cfg / app).glob("*") if p.is_dir())
        if not runs or not (runs[0] / "core.stat.0.out").exists():
            missing.append(app)
            continue
        run = runs[0]
        dst = out / app
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(run, dst, symlinks=False)
        cycles, insts, ipc = read_ipc(run)
        counters = read_counters(run)
        rows.append({"app": app, "cycles": cycles, "instructions": insts, "ipc": ipc,
                     **{c: counters.get(c, "") for c in IFUSE_COUNTERS}})

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["app", "cycles", "instructions", "ipc", *IFUSE_COUNTERS])
        w.writeheader()
        w.writerows(rows)

    for r in rows:
        print(f"{r['app']:12s} IPC {r['ipc']:.4f}  fused loads {r['IFUSE_FUSED_LOADS']}")
    if missing:
        print(f"Missing or unfinished: {' '.join(missing)}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
