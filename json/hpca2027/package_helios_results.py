#!/usr/bin/env python3
"""Write final Helios results package (accept speedup >= 1.0)."""
from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

APPS = [
    "appworld", "bfs-web-google", "bfs-init", "clickhouse", "corebench",
    "dfs-web-google", "dfs-init", "duckdb", "grpc", "leveldb", "memcached",
    "pagerank-gnutella31", "pagerank-init", "rocksdb", "sqlite",
    "sssp-ego-facebook", "sssp-init", "terminal_bench",
]
RESULTS = Path("/users/deepmish/helios-final-results")
BASE = Path("/users/deepmish/scarab/src/simulations/baseline")
HEL = Path("/users/deepmish/scarab/src/simulations/helios")
HELIOS_SH = Path("/users/deepmish/scarab-infra/json/hpca2027/helios.sh")


def avg_ipc(root: Path, app: str) -> float | None:
    d = root / app
    if not d.is_dir():
        return None
    ipcs = []
    for sp in sorted(d.iterdir()):
        if not sp.is_dir() or not sp.name.isdigit():
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


def mcpat_ok(root: Path, app: str) -> tuple[int, int]:
    d = root / app
    ok = total = 0
    if not d.is_dir():
        return 0, 0
    for sp in sorted(d.iterdir()):
        if not sp.is_dir() or not sp.name.isdigit():
            continue
        total += 1
        if (sp / "core.stat.0.out").is_file() and (sp / "mcpat.out").stat().st_size > 1000 and (
            sp / "power_model_results.out"
        ).is_file():
            ok += 1
    return ok, total


def read_map(name: str) -> dict[str, str]:
    text = HELIOS_SH.read_text()
    # HELIOS_T[app]=val or HELIOS_DO_FUSION
    m = re.search(rf"declare -A {name}=\((.*?)\)", text, re.S)
    if not m:
        return {}
    return dict(re.findall(r"\[([^\]]+)\]=(\S+)", m.group(1)))


def main() -> None:
    Ts = {k: int(v) for k, v in read_map("HELIOS_T").items()}
    fus = {k: int(v) for k, v in read_map("HELIOS_DO_FUSION").items()}
    rows = []
    for app in APPS:
        b = avg_ipc(BASE, app) or 0.0
        h = avg_ipc(HEL, app) or 0.0
        sp = (h / b) if b > 0 else 0.0
        mok, mtot = mcpat_ok(HEL, app)
        rows.append(
            {
                "app": app,
                "baseline_ipc": round(b, 6),
                "helios_ipc": round(h, 6),
                "speedup": round(sp, 6),
                "speedup_pct": round((sp - 1.0) * 100, 3),
                "helios_T": Ts.get(app, ""),
                "helios_do_fusion": fus.get(app, 1),
                "mcpat_ok": mok,
                "mcpat_total": mtot,
            }
        )

    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "ipc_speedup.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    lines = [
        "# Helios Final Results",
        "",
        "## Simulation setup",
        "- Traces: [deepanjalimishra99/ifuse-traces](https://huggingface.co/datasets/deepanjalimishra99/ifuse-traces)",
        "- Warmup: 20,000,000 instructions",
        "- Measured: 10,000,000 instructions (`inst_limit=30,000,000`)",
        "- Helios architecture: `in` (PARAMS.in / Golden Cove)",
        "- Baseline architecture: `golden_cove` (no Helios)",
        "- Always-on Helios knobs (when fusion enabled): `--helios_enable_flushes 1 "
        "--helios_fused_wait_tail_srcs 1 --helios_extended_commit_group 1`",
        "- Store fusion: **off** for all apps",
        "- Window W=64, increment I=1 (clickhouse I=10), decrement D=10",
        "",
        "## Power modeling",
        "- `--power_intf_on 1` on all runs",
        "- Container env: `MCPAT_BIN=$HOME/toolchain/bin/mcpat` "
        "`CACTI_BIN=$HOME/toolchain/bin/cacti` (glibc 2.31 builds)",
        "- McPAT includes **Helios Fusion Unit** (FP/selector/head/UCH) and **RFP Prefetch Unit** (PT/PAT)",
        "- McPAT XML emits 1 bank for I$/D$ (CACTI cannot size 8-bank 32KB I$)",
        "",
        "## Per-app Helios config",
        "",
        "| App | T | Fusion | Config |",
        "|-----|---|--------|--------|",
    ]
    for app in APPS:
        t = Ts.get(app, "?")
        f = fus.get(app, 1)
        i = 10 if app == "clickhouse" else 1
        fus_s = "on" if f else "off (matched baseline; fusion hurt IPC)"
        lines.append(f"| {app} | {t} | {fus_s} | T{t}/W64/I{i}/D10/stores-off |")

    lines += [
        "",
        "## IPC speedup (Helios / Baseline, equal-weight simpoint average)",
        "",
        "| App | Baseline IPC | Helios IPC | Speedup | Speedup % | McPAT |",
        "|-----|--------------|------------|---------|-----------|-------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['app']} | {r['baseline_ipc']:.4f} | {r['helios_ipc']:.4f} | "
            f"{r['speedup']:.4f} | {r['speedup_pct']:.2f}% | "
            f"{r['mcpat_ok']}/{r['mcpat_total']} |"
        )

    neg = [r["app"] for r in rows if r["speedup"] < 1.0 - 1e-9]
    lines += [
        "",
        "## Notes",
        "- `dfs-web-google` and `leveldb` used `--helios_do_fusion 0` after tuning could not "
        "find a beneficial confidence threshold (fusion remained harmful).",
        "- With fusion disabled those apps match baseline IPC (speedup = 1.0).",
        f"- Apps with speedup < 1.0: {neg if neg else 'none'}.",
        "",
    ]
    (RESULTS / "README.md").write_text("\n".join(lines))
    shutil.copy2(HELIOS_SH, RESULTS / "helios.sh.used")
    (RESULTS / "helios_per_app.json").write_text(
        json.dumps({"T": Ts, "do_fusion": fus}, indent=2) + "\n"
    )

    # Refresh simulation snapshot
    for exp, src in ("baseline", BASE), ("helios", HEL):
        dst = RESULTS / "simulations" / exp
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns("*.warmup", "ramulator.stat.out", "tmp", "logs"),
        )

    print("Wrote", RESULTS)
    for r in rows:
        print(
            f"  {r['app']:22s} speedup={r['speedup']:.4f} "
            f"fusion={r['helios_do_fusion']} mcpat={r['mcpat_ok']}/{r['mcpat_total']}"
        )
    if neg:
        raise SystemExit(f"still negative: {neg}")


if __name__ == "__main__":
    main()
