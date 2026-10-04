#!/usr/bin/env python3
"""Package hpca2027-revision baseline (ROB 352 vs 512) results into the scarab repo.

Output layout (``--out``):

    README.md          methodology + IPC table
    ipc.csv            per-app IPC for each ROB size and the speedup
    rob-352/<app>/     *.stat.0.csv, PARAMS.out, sim.log
    rob-512/<app>/     same
"""

import argparse
import csv
import math
import shutil
from pathlib import Path

CLUSTER_ID = "20"
INST_TARGET = 500_000_000
KEEP_SUFFIXES = (".stat.0.csv",)
KEEP_FILES = ("PARAMS.out", "sim.log")


def read_core_stats(run_dir: Path) -> dict:
    stats = {}
    with (run_dir / "core.stat.0.csv").open() as f:
        for row in csv.reader(f):
            if len(row) >= 3 and row[0].startswith("Cumulative_"):
                stats[row[0].strip()] = int(row[2].strip())
    return stats


def rob_label(config: str) -> str:
    # baseline-rob352 -> rob-352
    return "rob-" + config.rsplit("rob", 1)[1]


def geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim-root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--configs", nargs="+", required=True)
    ap.add_argument("--apps", nargs="+", required=True)
    args = ap.parse_args()

    small, large = sorted(args.configs, key=lambda c: int(c.rsplit("rob", 1)[1]))
    ipc = {c: {} for c in args.configs}
    problems = []

    if args.out.exists():
        shutil.rmtree(args.out)
    for config in args.configs:
        for app in args.apps:
            run_dir = args.sim_root / config / app / CLUSTER_ID
            if not (run_dir / "core.stat.0.csv").is_file():
                problems.append(f"{config}/{app}: no core.stat.0.csv (run missing or unfinished)")
                continue
            s = read_core_stats(run_dir)
            insts, cycles = s["Cumulative_Instructions"], s["Cumulative_Cycles"]
            if insts != INST_TARGET:
                problems.append(f"{config}/{app}: {insts:,} instructions (expected {INST_TARGET:,})")
            ipc[config][app] = insts / cycles

            dest = args.out / rob_label(config) / app
            dest.mkdir(parents=True)
            for f in sorted(run_dir.iterdir()):
                if f.name in KEEP_FILES or f.name.endswith(KEEP_SUFFIXES):
                    shutil.copy2(f, dest / f.name)

    if problems:
        print("Cannot package results:")
        for p in problems:
            print(f"  - {p}")
        return 1

    rows = []
    for app in args.apps:
        a, b = ipc[small][app], ipc[large][app]
        rows.append((app, a, b, b / a))
    gm = (geomean([r[1] for r in rows]), geomean([r[2] for r in rows]))
    gm_speedup = gm[1] / gm[0]

    s_lbl, l_lbl = rob_label(small), rob_label(large)
    with (args.out / "ipc.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", f"ipc_{s_lbl}", f"ipc_{l_lbl}", f"speedup_{l_lbl}_over_{s_lbl}"])
        for app, a, b, sp in rows:
            w.writerow([app, f"{a:.4f}", f"{b:.4f}", f"{sp:.4f}"])
        w.writerow(["geomean", f"{gm[0]:.4f}", f"{gm[1]:.4f}", f"{gm_speedup:.4f}"])

    s_n, l_n = s_lbl.split("-")[1], l_lbl.split("-")[1]
    table = "\n".join(
        f"| {app} | {a:.3f} | {b:.3f} | {(sp - 1) * 100:+.2f}% |" for app, a, b, sp in rows
    )
    readme = f"""# HPCA 2027 revision: Golden Cove baseline, ROB {s_n} vs {l_n}

## Setup

| | |
|---|---|
| Core | Golden Cove (`src/PARAMS.golden_cove`) |
| ROB {l_n} | Golden Cove as-is (`--node_table_size {l_n}`) |
| ROB {s_n} | Golden Cove with `--node_table_size {s_n}`; nothing else changed |
| Workloads | SPEC CPU2017 speed_int, Helios fixed-region traces ([dataset](https://huggingface.co/datasets/harry1332/helios-spec2017-fixed-region-20261002)) |
| Window | {INST_TARGET // 1_000_000}M instructions from instruction 1, no warmup (Helios methodology) |
| Scarab branch | `hpca2027-revision-baseline` |
| Launcher | scarab-infra `hpca2027-revision`: `json/hpca2027-revision/baseline.sh` |

## IPC

| Benchmark | ROB {s_n} | ROB {l_n} | ROB {l_n} speedup |
|---|---:|---:|---:|
{table}
| **Geomean** | **{gm[0]:.3f}** | **{gm[1]:.3f}** | **{(gm_speedup - 1) * 100:+.2f}%** |

IPC = `Cumulative_Instructions / Cumulative_Cycles` from `core.stat.0.csv`.
Machine-readable copy: [`ipc.csv`](ipc.csv).

## Top-down

![Top-down level 1](topdown/topdown_level1.png)

![Top-down level 2](topdown/topdown_level2.png)

Percentages are Scarab's `TOPDOWN_*_BOUND` counters (`core.stat.0.csv`); all
values are in [`topdown/topdown.csv`](topdown/topdown.csv).

## Backend stalls by resource

Paper style (same canvas and colors as `hpca2027-characterization/plot_backend_resource_stalls.py`;
ROB 352 solid, ROB 512 hatched):

![Backend stalls, paper style](backend_stalls/backend-resource-stalls.png)

![Backend stalls by resource](backend_stalls/backend_stalls.png)

ROB, LQ and SQ are below ~1% of cycles, so they are zoomed here:

![ROB, LQ, SQ stalls](backend_stalls/backend_stalls_rob_lq_sq.png)

% of cycles rename/allocation is blocked, by the full backend resource:
RAT (`MAP_STAGE_STALL_ITSELF`: no free physical register to rename into),
ROB / LQ / SQ (`MAP_STAGE_STALLED` split by `FULL_WINDOW_STALL`,
`LSQ_FULL_LOAD_QUEUE`, `LSQ_FULL_STORE_QUEUE`). Scarab never blocks
allocation on a full issue queue, so there is no IQ component. Values: [`backend_stalls/backend_stalls.csv`](backend_stalls/backend_stalls.csv).

## Layout

```
{s_lbl}/<benchmark>/   Scarab stats (*.stat.0.csv), PARAMS.out, sim.log
{l_lbl}/<benchmark>/   same, for ROB {l_n}
topdown/               top-down figures (PNG + PDF) and topdown.csv
backend_stalls/        backend stall-by-resource figures (PNG + PDF) and CSV
ipc.csv
```

## Reproduce

```bash
cd ~/scarab-infra && git checkout hpca2027-revision
./json/hpca2027-revision/baseline.sh            # register traces + run
./json/hpca2027-revision/baseline.sh --status
./json/hpca2027-revision/baseline.sh --package  # regenerate this directory
```
"""
    (args.out / "README.md").write_text(readme)
    print(f"Packaged {len(rows)} benchmarks x {len(args.configs)} configs into {args.out}")
    print(f"Geomean IPC: {s_lbl} {gm[0]:.4f}, {l_lbl} {gm[1]:.4f} ({(gm_speedup - 1) * 100:+.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
