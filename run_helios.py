#!/usr/bin/env python3
"""One-command HELIOS experiment runner.

Reads json/HELIOS.json and, for each optimal-tuning group (runs[]):
  1. materializes the group descriptor to json/<experiment>.json,
  2. builds scarab once if the cached binary is missing,
  3. runs `./sci --sim / --collect-stats / --visualize <experiment>`,
and finally runs helios_plots.py to emit benefit_latency.png + fusion_breakdown.png
into the campaign root_dir. So the two graphs are produced automatically at the end of
every run — no separate plotting step.

Prereqs (see REPRODUCE.md): `./sci --init` (Docker + conda) and the trace wiring under
$TRACES_DIR/helios_dc/helios_dc/<app>/. Run from the scarab-infra dir: `python3 run_helios.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = json.loads((HERE / "json" / "HELIOS.json").read_text())
RUNS = BUNDLE["runs"]


def sci(*args: str) -> int:
    print(f"$ ./sci {' '.join(args)}", flush=True)
    rc = subprocess.run(["./sci", *args], cwd=HERE).returncode
    if rc != 0:
        print(f"  WARNING: ./sci {' '.join(args)} exited {rc}", file=sys.stderr)
    return rc


def main() -> None:
    # 1. materialize each group descriptor (gitignored: json/HELIOS_*.json)
    for d in RUNS:
        (HERE / "json" / f"{d['experiment']}.json").write_text(json.dumps(d, indent=2))
    exps = [d["experiment"] for d in RUNS]
    print(f"Materialized {len(exps)} group descriptors: {', '.join(exps)}")

    # 2. wire the downloaded traces into the helios_dc suite layout sci expects
    #    (idempotent — no-op if already wired; needs the HF dataset downloaded first)
    print("Wiring traces into the helios_dc suite layout…")
    subprocess.run([sys.executable, str(HERE / "wire_helios_traces.py")], cwd=HERE, check=False)

    # 3. build scarab once if the cached binary isn't present
    if not (HERE / "scarab_builds" / "scarab_current.opt").exists():
        print("scarab binary not cached — building from the descriptor's scarab_path…")
        sci("--build-scarab", exps[0])

    # 4. run every group: sim -> collect-stats -> visualize
    for e in exps:
        for action in ("--sim", "--collect-stats", "--visualize"):
            sci(action, e)

    # 5. graphs (benefit_latency.png + fusion_breakdown.png in the campaign root_dir)
    print("Generating graphs (helios_plots.py)…")
    subprocess.run([sys.executable, str(HERE / "helios_plots.py")], cwd=HERE, check=False)


if __name__ == "__main__":
    main()
