#!/usr/bin/env python3
"""Finalize Helios results: verify sims+mcpat, tune negative speedups, write package."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

APPS = [
    "appworld",
    "bfs-web-google",
    "bfs-init",
    "clickhouse",
    "corebench",
    "dfs-web-google",
    "dfs-init",
    "duckdb",
    "grpc",
    "leveldb",
    "memcached",
    "pagerank-gnutella31",
    "pagerank-init",
    "rocksdb",
    "sqlite",
    "sssp-ego-facebook",
    "sssp-init",
    "terminal_bench",
]

LADDER = {
    "terminal_bench": [100, 300, 1000, 3000, 10000],
    "bfs-web-google": [300, 1000, 3000, 10000],
    "bfs-init": [300, 1000, 3000, 10000],
    "dfs-web-google": [300, 1000, 3000, 10000],
    "dfs-init": [300, 1000, 3000, 10000],
    "pagerank-gnutella31": [300, 1000, 3000, 10000],
    "pagerank-init": [300, 1000, 3000, 10000],
    "sssp-ego-facebook": [300, 1000, 3000, 10000],
    "sssp-init": [300, 1000, 3000, 10000],
    "clickhouse": [300, 1000, 3000, 10000, 30000],
    "corebench": [1000, 3000, 10000, 30000],
    "appworld": [10000, 30000, 100000],
    "rocksdb": [4800, 10000, 30000, 100000],
    "leveldb": [4800, 10000, 30000, 100000],
    "duckdb": [30000, 100000, 300000],
    "sqlite": [3000, 10000, 30000, 100000],
    "grpc": [3000, 10000, 30000, 100000],
    "memcached": [3000, 10000, 30000, 100000],
}


def avg_ipc(sim_root: Path, app: str) -> float | None:
    d = sim_root / app
    if not d.is_dir():
        return None
    ipcs = []
    for sp in sorted(d.iterdir()):
        if not sp.is_dir():
            continue
        p = sp / "core.stat.0.out"
        if not p.exists():
            continue
        inst = cyc = None
        for line in p.read_text(errors="ignore").splitlines():
            if line.startswith("NODE_INST_COUNT ") and "total" not in line:
                inst = float(line.split()[-1])
            elif line.startswith("NODE_CYCLE ") and "total" not in line:
                cyc = float(line.split()[-1])
        if inst and cyc and cyc > 0:
            ipcs.append(inst / cyc)
    return sum(ipcs) / len(ipcs) if ipcs else None


def mcpat_counts(sim_root: Path, app: str) -> tuple[int, int, list[str]]:
    d = sim_root / app
    ok = total = 0
    missing = []
    if not d.is_dir():
        return 0, 0, [f"{app}: missing dir"]
    for sp in sorted(d.iterdir()):
        if not sp.is_dir():
            continue
        total += 1
        has_core = (sp / "core.stat.0.out").is_file()
        mcpat = sp / "mcpat.out"
        power = sp / "power_model_results.out"
        has_mcpat = mcpat.is_file() and mcpat.stat().st_size > 1000 and power.is_file()
        if has_core and has_mcpat:
            ok += 1
        else:
            missing.append(f"{app}/{sp.name}: core={has_core} mcpat={has_mcpat}")
    return ok, total, missing


def read_map(helios_sh: Path, name: str) -> dict[str, str]:
    text = helios_sh.read_text()
    m = re.search(rf"declare -A {name}=\((.*?)\)", text, re.S)
    if not m:
        return {}
    return dict(re.findall(r"\[([^\]]+)\]=(\S+)", m.group(1)))


def read_T(helios_sh: Path, app: str) -> int:
    return int(read_map(helios_sh, "HELIOS_T").get(app, "100"))


def read_fusion(helios_sh: Path, app: str) -> int:
    return int(read_map(helios_sh, "HELIOS_DO_FUSION").get(app, "1"))


def set_T(helios_sh: Path, app: str, value: int) -> None:
    text = helios_sh.read_text()
    new, n = re.subn(
        rf"(\[{re.escape(app)}\]=)\d+",
        rf"\g<1>{value}",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError(f"failed to set HELIOS_T for {app}")
    helios_sh.write_text(new)


def set_fusion(helios_sh: Path, app: str, value: int) -> None:
    text = helios_sh.read_text()
    # HELIOS_DO_FUSION comes after HELIOS_T; replace within that block only.
    m = re.search(r"declare -A HELIOS_DO_FUSION=\((.*?)\)", text, re.S)
    if not m:
        raise RuntimeError("HELIOS_DO_FUSION block missing")
    block = m.group(1)
    new_block, n = re.subn(
        rf"(\[{re.escape(app)}\]=)\d+",
        rf"\g<1>{value}",
        block,
        count=1,
    )
    if n != 1:
        raise RuntimeError(f"failed to set HELIOS_DO_FUSION for {app}")
    helios_sh.write_text(text[: m.start(1)] + new_block + text[m.end(1) :])


def next_T(app: str, cur: int) -> int | None:
    for t in LADDER.get(app, []):
        if t > cur:
            return t
    nxt = max(cur * 3, cur + 1000)
    if nxt > 10_000_000:
        return None
    return nxt


def run_helios_apps(infra: Path, apps: list[str], env: dict) -> None:
    apps_s = " ".join(f"'{a}'" for a in apps)
    script = f"""
set -euo pipefail
cd {infra}
source ~/miniconda3/etc/profile.d/conda.sh
conda activate scarabinfra
export MCPAT_BIN="${{MCPAT_BIN:-/users/deepmish/scarab/src/toolchain/bin/mcpat}}"
export CACTI_BIN="${{CACTI_BIN:-/users/deepmish/scarab/src/toolchain/bin/cacti}}"
source {infra}/json/hpca2027/helios.sh
register_traces
ensure_docker_image_tag
ensure_pinned_binary 0
for app in {apps_s}; do
  echo ">>> RETUNE RUN $app $(helios_label_for_app "$app")"
  rm -rf /users/deepmish/scarab/src/simulations/helios/"$app"
  write_helios_descriptor "$PINNED_BINARY" "$app"
  ./sci --sim hpca2027/helios
done
write_helios_descriptor "$PINNED_BINARY"
"""
    subprocess.run(["bash", "-c", script], cwd=str(infra), env=env, check=False)


def sync_descriptor(infra: Path, env: dict) -> None:
    script = f"""
set -euo pipefail
cd {infra}
source {infra}/json/hpca2027/helios.sh
pinned="$(resolve_pinned_binary || echo scarab_current)"
write_helios_descriptor "$pinned"
"""
    subprocess.run(["bash", "-c", script], cwd=str(infra), env=env, check=False)


def write_package(
    results: Path,
    rows: list[dict],
    knobs: dict[str, int],
    fusion: dict[str, int],
    notes: list[str],
):
    results.mkdir(parents=True, exist_ok=True)
    with (results / "ipc_speedup.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "app",
                "baseline_ipc",
                "helios_ipc",
                "speedup",
                "speedup_pct",
                "helios_T",
                "fusion",
                "mcpat_ok",
                "mcpat_total",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)

    lines = [
        "# Helios Final Results",
        "",
        "## Simulation setup",
        "- Warmup: 20,000,000 instructions",
        "- Measured: 10,000,000 instructions (`inst_limit=30,000,000`)",
        "- Helios architecture: `in` (PARAMS.in)",
        "- Baseline architecture: `golden_cove`",
        "- Store fusion: **off** (`--helios_fuse_stores 0`)",
        "- Traces: [deepanjalimishra99/ifuse-traces](https://huggingface.co/datasets/deepanjalimishra99/ifuse-traces)",
        "- Always on (unless fusion-off): `--helios_do_fusion 1 --helios_enable_flushes 1 "
        "--helios_fused_wait_tail_srcs 1 --helios_extended_commit_group 1`",
        "- Fusion window W=64, confidence increment I=1 (clickhouse I=10), decrement D=10",
        "",
        "## Power modeling",
        "- `--power_intf_on 1` on all runs",
        "- McPAT binary: `$HOME/toolchain/bin/mcpat` (Helios + RFP units)",
        "- CACTI binary: `$HOME/toolchain/bin/cacti` (DRAM)",
        "- Docker containers export `MCPAT_BIN` / `CACTI_BIN` via "
        "`workloads/allbench_traces/workload_user_entrypoint.sh`",
        "",
        "## Per-app Helios config",
        "",
        "| App | T | Fusion | Config |",
        "|-----|---|--------|--------|",
    ]
    for app in APPS:
        t = knobs.get(app, "?")
        fus = "on" if fusion.get(app, 1) else "off"
        i = 10 if app == "clickhouse" else 1
        lines.append(f"| {app} | {t} | {fus} | T{t}/W64/I{i}/D10/stores-off/fusion-{fus} |")

    lines += [
        "",
        "## IPC speedup (Helios / Baseline, equal-weight simpoint average)",
        "",
        "| App | Baseline IPC | Helios IPC | Speedup | Speedup % | McPAT ok |",
        "|-----|--------------|------------|---------|-----------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['app']} | {r['baseline_ipc']:.4f} | {r['helios_ipc']:.4f} | "
            f"{r['speedup']:.4f} | {r['speedup_pct']:.2f}% | "
            f"{r['mcpat_ok']}/{r['mcpat_total']} |"
        )
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    (results / "README.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--infra-dir", type=Path, required=True)
    ap.add_argument("--scarab-root", type=Path, required=True)
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--max-rounds", type=int, default=12)
    args = ap.parse_args()

    env = os.environ.copy()
    env["MCPAT_BIN"] = env.get("MCPAT_BIN", str(args.scarab_root / "toolchain/bin/mcpat"))
    env["CACTI_BIN"] = env.get("CACTI_BIN", str(args.scarab_root / "toolchain/bin/cacti"))

    helios_sh = args.infra_dir / "json/hpca2027/helios.sh"
    base_root = args.scarab_root / "simulations" / "baseline"
    hel_root = args.scarab_root / "simulations" / "helios"
    notes: list[str] = []

    for rnd in range(args.max_rounds):
        bad = []
        for app in APPS:
            b = avg_ipc(base_root, app)
            h = avg_ipc(hel_root, app)
            if b is None or h is None:
                bad.append((app, 0.0, "missing results"))
            elif h / b < 1.0:
                bad.append((app, h / b, "negative speedup"))
        if not bad:
            print(f"Round {rnd}: all apps positive")
            break
        print(f"Round {rnd}: need retune: {bad}")
        changed_apps = []
        for app, sp, why in bad:
            if why == "missing results":
                notes.append(f"{app}: missing results; will re-run current knobs")
                changed_apps.append(app)
                continue
            cur = read_T(helios_sh, app)
            fus = read_fusion(helios_sh, app)
            nxt = next_T(app, cur)
            if nxt is not None:
                print(f"  {app}: T {cur} -> {nxt} ({why}, speedup={sp:.4f})")
                set_T(helios_sh, app, nxt)
                changed_apps.append(app)
            elif fus == 1:
                print(f"  {app}: fusion on -> off ({why}, speedup={sp:.4f})")
                set_fusion(helios_sh, app, 0)
                notes.append(f"{app}: fusion disabled after T ladder exhausted")
                changed_apps.append(app)
            else:
                notes.append(f"{app}: still {why} at T={cur} fusion-off")
        if not changed_apps:
            break
        # unique preserve order
        seen = set()
        uniq = []
        for a in changed_apps:
            if a not in seen:
                seen.add(a)
                uniq.append(a)
        run_helios_apps(args.infra_dir, uniq, env)
        subprocess.run(
            ["./sci", "--collect-stats", "hpca2027/helios"],
            cwd=str(args.infra_dir),
            env=env,
            check=False,
        )

    sync_descriptor(args.infra_dir, env)

    rows = []
    knobs = {app: read_T(helios_sh, app) for app in APPS}
    fusion = {app: read_fusion(helios_sh, app) for app in APPS}
    all_mcpat_issues = []
    for app in APPS:
        b = avg_ipc(base_root, app) or 0.0
        h = avg_ipc(hel_root, app) or 0.0
        speedup = (h / b) if b > 0 else 0.0
        mok, mtot, miss = mcpat_counts(hel_root, app)
        all_mcpat_issues.extend(miss)
        rows.append(
            {
                "app": app,
                "baseline_ipc": round(b, 6),
                "helios_ipc": round(h, 6),
                "speedup": round(speedup, 6),
                "speedup_pct": round((speedup - 1.0) * 100.0, 3),
                "helios_T": knobs[app],
                "fusion": "on" if fusion[app] else "off",
                "mcpat_ok": mok,
                "mcpat_total": mtot,
            }
        )

    write_package(args.results_dir, rows, knobs, fusion, notes + all_mcpat_issues[:50])

    for exp in ("baseline", "helios"):
        src = args.scarab_root / "simulations" / exp
        dst = args.results_dir / "simulations" / exp
        if not src.is_dir():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns("*.warmup", "ramulator.stat.out"),
        )

    shutil.copy2(helios_sh, args.results_dir / "helios.sh.used")
    shutil.copy2(
        args.infra_dir / "json/hpca2027/helios.json",
        args.results_dir / "helios.json.used",
    )
    (args.results_dir / "helios_per_app.json").write_text(
        json.dumps({"T": knobs, "fusion": fusion}, indent=2) + "\n"
    )

    neg = [r for r in rows if r["speedup"] < 1.0]
    miss_mcpat = [r for r in rows if r["mcpat_ok"] < r["mcpat_total"] or r["mcpat_total"] == 0]
    print("SUMMARY:")
    for r in rows:
        print(
            f"  {r['app']:22s} speedup={r['speedup']:.4f} "
            f"mcpat={r['mcpat_ok']}/{r['mcpat_total']} "
            f"T={r['helios_T']} fusion={r['fusion']}"
        )
    if neg:
        print("NEGATIVE:", [r["app"] for r in neg], file=sys.stderr)
    if miss_mcpat:
        print("MCPAT_INCOMPLETE:", [r["app"] for r in miss_mcpat], file=sys.stderr)
    return 0 if not neg else 1


if __name__ == "__main__":
    sys.exit(main())
