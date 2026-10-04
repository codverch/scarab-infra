#!/usr/bin/env python3
"""Copy every baseline Helios paper-config run into scarab/src/hpca2027-revision/helios-paper-config-baseline/.

Reads <root_dir>/simulations/<config>/<app>/<cluster>/ for the single config in the
descriptor and writes:

  <out>/<app>/            every file from the run dir (all *.stat.0.out/.csv, PARAMS.in,
                          PARAMS.out, sim.log, ...)
  <out>/summary.csv       cycles, instructions, and IPC per app

Usage: package_helios_paper_baseline_results.py --descriptor json/hpca2027-revision/helios_paper_baseline.json \
           [--out ~/scarab/src/hpca2027-revision/helios-paper-config-baseline]
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from package_helios_paper_results import read_ipc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--descriptor", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    desc = json.loads(args.descriptor.read_text())
    out = args.out or Path(desc["scarab_path"]) / "src/hpca2027-revision/helios-paper-config-baseline"
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
        rows.append({"app": app, "cycles": cycles, "instructions": insts, "ipc": ipc})

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["app", "cycles", "instructions", "ipc"])
        w.writeheader()
        w.writerows(rows)

    for r in rows:
        print(f"{r['app']:12s} IPC {r['ipc']:.4f}")
    if missing:
        print(f"Missing or unfinished: {' '.join(missing)}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
