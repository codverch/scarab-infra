#!/usr/bin/env python3
"""Copy every ideal-fusion Helios paper-config run into scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion/.

Reads <root_dir>/simulations/<config>/<app>/<cluster>/ for both passes and writes:

  <out>/<app>/            every file from the pass-2 (ideal fusion) run dir (all
                          *.stat.0.out/.csv, PARAMS.in, PARAMS.out, sim.log, ...);
                          sim.log drops heartbeat and unmapped-instruction lines
  <out>/no-fusion/<app>/  the same for the pass-1 run, the no-fusion baseline
  <out>/summary.csv       pass-1 (no-fusion) and pass-2 IPC, speedup and fusion counters per app

IPC is Periodic_Instructions / Periodic_Cycles from core.stat.0.csv: the
measured region after the 20M-instruction warmup.

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

from package_helios_paper_results import read_counters

MEASURED_INSTS = 100_000_000

FUSION_COUNTERS = (
    "IDEAL_FUSION_FUSED_LOADS",
    "IDEAL_FUSION_LOADS_PARTICIPATED",
    "IDEAL_FUSION_LOAD2_BYPASSED",
    "ONPATH_MEM_LOADS",
    # Non-zero only with --ideal_fusion_stores.
    "IDEAL_FUSION_FUSED_STORES",
    "IDEAL_FUSION_STORES_PARTICIPATED",
    "ONPATH_MEM_STORES",
)


def read_ipc(run: Path) -> tuple[int, int, float]:
    """Cycles, instructions and IPC of the measured (post-warmup) region."""
    stats = {}
    for line in (run / "core.stat.0.csv").read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0] in ("Periodic_Cycles", "Periodic_Instructions"):
            stats[parts[0]] = int(parts[2])
    cycles, insts = stats["Periodic_Cycles"], stats["Periodic_Instructions"]
    # The warmup cutoff lands a few instructions past 20M, so allow a small slack.
    if abs(insts - MEASURED_INSTS) > 1000:
        print(f"WARNING: {run} measured {insts:,} instructions (expected {MEASURED_INSTS:,})")
    return cycles, insts, insts / cycles


TRIM_PREFIXES = ("** Heartbeat", "Unmapped instruction", "Not correct inst")


def trim_sim_log(path: Path) -> None:
    """Drop heartbeat and decoder-warning lines, which make sim.log ~100 MB."""
    kept, dropped = [], 0
    with path.open(errors="replace") as f:
        for line in f:
            if line.startswith(TRIM_PREFIXES):
                dropped += 1
            else:
                kept.append(line)
    kept.append(f"[trimmed: dropped {dropped} heartbeat / unmapped-instruction lines]\n")
    path.write_text("".join(kept))


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
        trim_sim_log(dst / "sim.log")
        base = out / "no-fusion" / app
        if base.exists():
            shutil.rmtree(base)
        shutil.copytree(r1, base, symlinks=False)
        trim_sim_log(base / "sim.log")
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
