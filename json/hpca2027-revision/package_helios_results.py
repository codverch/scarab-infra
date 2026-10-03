#!/usr/bin/env python3
"""Package the Helios ROB-sensitivity runs into a tidy, self-describing results dir.

Reads <root_dir>/simulations/<config>/<workload>/<cluster>/ for every config and
workload in the descriptor and writes:

  <out>/README.md          methodology, configs, headline tables
  <out>/ipc.csv            IPC per workload x config
  <out>/speedup.csv        Helios speedup at each ROB size, ROB 352 vs 512 baseline
  <out>/helios_stats.csv   Helios fusion counters per workload x Helios config
  <out>/ipc_speedup.png    Helios speedup per workload at ROB 512 and 352
  <out>/runs/<config>/<workload>/   *.stat.0.out, PARAMS.out, sim.log

Usage: package_helios_results.py --descriptor json/hpca2027-revision/helios.json --out <dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

ROBS = (512, 352)
HELIOS_COUNTERS = (
    "HELIOS_ONPATH_LOADS",
    "HELIOS_FUSIONS_COMMITTED",
    "HELIOS_FUSION_MISPREDICT",
    "HELIOS_FLUSHES",
)
RAW_FILES = ("PARAMS.out", "sim.log")


def read_ipc(run: Path) -> tuple[int, int, float]:
    text = (run / "core.stat.0.out").read_text()
    m = re.search(r"Cumulative:\s+Cycles:\s+(\d+)\s+Instructions:\s+(\d+)\s+IPC:\s+([\d.]+)", text)
    if not m:
        raise ValueError(f"no Cumulative line in {run / 'core.stat.0.out'}")
    return int(m[1]), int(m[2]), float(m[3])


def read_core_csv(run: Path) -> dict[str, int]:
    stats = {}
    for line in (run / "core.stat.0.csv").read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[0].endswith("_total_count"):
            try:
                stats[parts[0][: -len("_total_count")]] = int(parts[2])
            except ValueError:
                pass
    return stats


def geomean(xs: list[float]) -> float:
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def pct(x: float) -> str:
    return f"{(x - 1) * 100:+.2f}%"


def git_rev(path: str) -> str:
    out = subprocess.run(["git", "-C", path, "log", "-1", "--format=%h %s"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def git_branch(path: str) -> str:
    out = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def plot(out: Path, workloads: list[str], speedup: dict) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available; skipping plot")
        return
    labels = workloads + ["geomean"]
    x = range(len(labels))
    w = 0.38
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for i, (rob, color) in enumerate(zip(ROBS, ("#2c6fbb", "#e08a2c"))):
        vals = [(speedup[wl][rob] - 1) * 100 for wl in workloads]
        vals.append((geomean([speedup[wl][rob] for wl in workloads]) - 1) * 100)
        ax.bar([p + (i - 0.5) * w for p in x], vals, w, label=f"ROB {rob}", color=color)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(list(x), labels, rotation=20)
    ax.set_ylabel("Helios speedup over\nsame-ROB baseline (%)")
    ax.set_title("Helios on SPEC CPU2017 speed int, Golden Cove, ROB 512 vs 352")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "ipc_speedup.png", dpi=200)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--descriptor", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    desc = json.loads(args.descriptor.read_text())
    sims_root = Path(desc["root_dir"]) / "simulations"
    sim = desc["simulations"][0]
    workloads = sim["workload"]
    configs = list(desc["configurations"])
    out = args.out
    if (out / "runs").exists():
        shutil.rmtree(out / "runs")
    out.mkdir(parents=True, exist_ok=True)

    ipc, helios_rows, missing = {}, [], []
    for wl in workloads:
        for cfg in configs:
            runs = sorted(p for p in (sims_root / cfg / wl).glob("*") if p.is_dir())
            if not runs or not (runs[0] / "core.stat.0.out").exists():
                missing.append(f"{cfg}/{wl}")
                continue
            run = runs[0]
            cycles, insts, value = read_ipc(run)
            ipc[(wl, cfg)] = (cycles, insts, value)
            if "helios" in cfg:
                s = read_core_csv(run)
                loads = s.get("HELIOS_ONPATH_LOADS", 0)
                fused = s.get("HELIOS_FUSIONS_COMMITTED", 0)
                helios_rows.append({
                    "workload": wl, "config": cfg,
                    **{k.lower(): s.get(k, 0) for k in HELIOS_COUNTERS},
                    "fused_load_pct": f"{100 * fused / loads:.2f}" if loads else "",
                })
            dst = out / "runs" / cfg / wl
            dst.mkdir(parents=True)
            for f in sorted(run.glob("*.stat.0.out")) + [run / n for n in RAW_FILES]:
                if f.exists():
                    shutil.copy2(f, dst / f.name)
    if missing:
        raise SystemExit(f"Missing/unfinished runs: {', '.join(missing)}")

    with (out / "ipc.csv").open("w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["workload", "config", "rob", "helios", "cycles", "instructions", "ipc"])
        for wl in workloads:
            for cfg in configs:
                cycles, insts, value = ipc[(wl, cfg)]
                rob = int(re.search(r"rob(\d+)", cfg)[1])
                wr.writerow([wl, cfg, rob, int("helios" in cfg), cycles, insts, f"{value:.5f}"])

    speedup = {wl: {rob: ipc[(wl, f"rob{rob}_helios")][2] / ipc[(wl, f"rob{rob}_baseline")][2]
                    for rob in ROBS} for wl in workloads}
    rob_effect = {wl: ipc[(wl, "rob352_baseline")][2] / ipc[(wl, "rob512_baseline")][2]
                  for wl in workloads}
    with (out / "speedup.csv").open("w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["workload", "helios_speedup_rob512", "helios_speedup_rob352",
                     "baseline_rob352_vs_rob512"])
        for wl in workloads:
            wr.writerow([wl, f"{speedup[wl][512]:.5f}", f"{speedup[wl][352]:.5f}",
                         f"{rob_effect[wl]:.5f}"])
        wr.writerow(["geomean", f"{geomean([speedup[w][512] for w in workloads]):.5f}",
                     f"{geomean([speedup[w][352] for w in workloads]):.5f}",
                     f"{geomean(list(rob_effect.values())):.5f}"])

    with (out / "helios_stats.csv").open("w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(helios_rows[0]))
        wr.writeheader()
        wr.writerows(helios_rows)

    plot(out, workloads, speedup)

    # README
    p = desc["configurations"]
    lines = [
        "# Helios ROB-size sensitivity: SPEC CPU2017 speed int",
        "",
        "Does Helios still help when the Golden Cove ROB shrinks from 512 to 352 entries?",
        "",
        "## Setup",
        "",
        "| | |",
        "|---|---|",
        f"| Simulator | Scarab, branch `{git_branch(desc['scarab_path'])}` @ `{git_rev(desc['scarab_path'])}` |",
        "| Core | Golden Cove (`src/PARAMS.in`); only `--node_table_size` (ROB) is changed |",
        "| Helios | `--helios_do_fusion 1`, all other Helios knobs at branch defaults |",
        "| Workloads | " + ", ".join(f"`{w}`" for w in workloads) + " |",
        "| Region | One fixed region per benchmark; first **500M instructions**, no warmup |",
        "| Runner | scarab-infra `hpca2027-revision`: `json/hpca2027-revision/helios.sh` |",
        "",
        "| Config | Scarab params |",
        "|---|---|",
        *[f"| `{c}` | `{p[c]['params']}` |" for c in configs],
        "",
        "## Results",
        "",
        "Speedup = IPC(Helios) / IPC(baseline) at the same ROB size. Geomean is over all",
        "7 traces with equal weight (gcc_s contributes 3 inputs).",
        "",
        "| Workload | IPC base 512 | IPC Helios 512 | IPC base 352 | IPC Helios 352 "
        "| Helios @512 | Helios @352 | ROB 352 vs 512 (base) |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for wl in workloads:
        i = {c: ipc[(wl, c)][2] for c in configs}
        lines.append(
            f"| {wl} | {i['rob512_baseline']:.3f} | {i['rob512_helios']:.3f} "
            f"| {i['rob352_baseline']:.3f} | {i['rob352_helios']:.3f} "
            f"| {pct(speedup[wl][512])} | {pct(speedup[wl][352])} | {pct(rob_effect[wl])} |")
    lines.append(
        f"| **geomean** | | | | | **{pct(geomean([speedup[w][512] for w in workloads]))}** "
        f"| **{pct(geomean([speedup[w][352] for w in workloads]))}** "
        f"| **{pct(geomean(list(rob_effect.values())))}** |")
    lines += [
        "",
        "![Helios speedup](ipc_speedup.png)",
        "",
        "### Helios fusion activity",
        "",
        "| Workload | Config | On-path loads | Fused (committed) | Fused % of loads | Mispredicts | Flushes |",
        "|---|---|--:|--:|--:|--:|--:|",
        *[f"| {r['workload']} | {r['config']} | {r['helios_onpath_loads']:,} "
          f"| {r['helios_fusions_committed']:,} | {r['fused_load_pct']} "
          f"| {r['helios_fusion_mispredict']:,} | {r['helios_flushes']:,} |" for r in helios_rows],
        "",
        "## Files",
        "",
        "| File | Contents |",
        "|---|---|",
        "| `ipc.csv` | Cycles, instructions, IPC for every workload x config |",
        "| `speedup.csv` | Helios speedup at ROB 512 / 352 and ROB 352 vs 512 baseline, plus geomean |",
        "| `helios_stats.csv` | Helios fusion counters for the Helios configs |",
        "| `ipc_speedup.png` | Helios speedup per workload at both ROB sizes |",
        "| `runs/<config>/<workload>/` | Raw Scarab stats (`*.stat.0.out`), `PARAMS.out`, `sim.log` |",
        "",
        "## Reproduce",
        "",
        "```bash",
        "# scarab: git checkout hpca2027-revision-helios",
        "# scarab-infra: git checkout hpca2027-revision",
        "cd scarab-infra",
        "./json/hpca2027-revision/helios.sh                      # register traces, build, simulate",
        "./json/hpca2027-revision/helios.sh --package <out_dir>  # regenerate this directory",
        "```",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
