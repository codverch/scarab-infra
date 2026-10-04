#!/usr/bin/env python3
"""Copy every ideal-fusion Helios paper-config run into scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion/.

Reads <root_dir>/simulations/<config>/<app>/<cluster>/ for both passes and writes:

  <out>/<app>/            every file from the pass-2 (ideal fusion) run dir (all
                          *.stat.0.out/.csv, PARAMS.in, PARAMS.out, sim.log, ...)
  <out>/summary.csv       pass-1 (no-fusion) and pass-2 IPC, speedup and fusion counters per app

Usage: package_helios_paper_ideal_fusion_results.py \
           --pass1 json/hpca2027-revision/helios_paper_ideal_fusion_pass1.json \
           --pass2 json/hpca2027-revision/helios_paper_ideal_fusion_pass2.json \
           [--out ~/scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion]
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from package_helios_paper_results import read_counters, read_ipc

FUSION_COUNTERS = (
    "IDEAL_FUSION_FUSED_LOADS",
    "IDEAL_FUSION_LOADS_PARTICIPATED",
    "IDEAL_FUSION_LOAD2_BYPASSED",
    "ONPATH_MEM_LOADS",
)


def run_dir(desc: dict, app: str) -> Path | None:
    (cfg,) = desc["configurations"]
    runs = sorted(p for p in (Path(desc["root_dir"]) / "simulations" / cfg / app).glob("*") if p.is_dir())
    if runs and (runs[0] / "core.stat.0.out").exists():
        return runs[0]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass1", required=True, type=Path)
    ap.add_argument("--pass2", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    p1 = json.loads(args.pass1.read_text())
    p2 = json.loads(args.pass2.read_text())
    out = args.out or Path(p2["scarab_path"]) / "src/hpca2027-revision/helios-paper-config-ideal-fusion"
    apps = [a for sim in p2["simulations"] for a in sim["workload"]]

    rows, missing = [], []
    for app in apps:
        r1, r2 = run_dir(p1, app), run_dir(p2, app)
        if r1 is None or r2 is None:
            missing.append(app)
            continue
        dst = out / app
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(r2, dst, symlinks=False)
        _, _, ipc1 = read_ipc(r1)
        cycles, insts, ipc2 = read_ipc(r2)
        counters = read_counters(r2)
        rows.append({"app": app, "cycles": cycles, "instructions": insts,
                     "ipc_no_fusion": ipc1, "ipc_ideal_fusion": ipc2, "speedup": ipc2 / ipc1,
                     **{c: counters.get(c, "") for c in FUSION_COUNTERS}})

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "summary.csv", "w", newline="") as f:
        fields = ["app", "cycles", "instructions", "ipc_no_fusion", "ipc_ideal_fusion", "speedup",
                  *FUSION_COUNTERS]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    for r in rows:
        print(f"{r['app']:12s} IPC {r['ipc_no_fusion']:.4f} -> {r['ipc_ideal_fusion']:.4f} "
              f"({(r['speedup'] - 1) * 100:+.2f}%)  fused loads {r['IDEAL_FUSION_FUSED_LOADS']}")
    if missing:
        print(f"Missing or unfinished: {' '.join(missing)}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
