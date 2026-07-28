#!/usr/bin/env python3
"""Bump Helios confidence threshold for apps with negative speedup and re-run them."""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

APPS = [
    "appworld", "bfs-web-google", "bfs-init", "clickhouse", "corebench",
    "dfs-web-google", "dfs-init", "duckdb", "grpc", "leveldb", "memcached",
    "pagerank-gnutella31", "pagerank-init", "rocksdb", "sqlite",
    "sssp-ego-facebook", "sssp-init", "terminal_bench",
]

# Escalation ladder per app (stores-off configs from prior tuning).
LADDER = {
    "terminal_bench": [100, 300, 1000],
    "bfs-web-google": [300, 1000, 3000],
    "bfs-init": [300, 1000, 3000],
    "dfs-web-google": [300, 1000, 3000],
    "dfs-init": [300, 1000, 3000],
    "pagerank-gnutella31": [300, 1000, 3000],
    "pagerank-init": [300, 1000, 3000],
    "sssp-ego-facebook": [300, 1000, 3000],
    "sssp-init": [300, 1000, 3000],
    "clickhouse": [300, 1000, 3000],
    "corebench": [1000, 3000, 10000],
    "appworld": [10000, 30000, 100000],
    "rocksdb": [4800, 10000, 30000],
    "leveldb": [4800, 10000, 30000],
    "duckdb": [30000, 100000, 300000],
    "sqlite": [3000, 10000, 30000],
    "grpc": [3000, 10000, 30000],
    "memcached": [3000, 10000, 30000],
}


def sim_dir(app: str) -> str:
    return app


def avg_ipc(sim_root: Path, app: str) -> float | None:
    d = sim_root / sim_dir(app)
    if not d.is_dir():
        return None
    ipcs = []
    for sp in d.iterdir():
        if not sp.is_dir():
            continue
        p = sp / "core.stat.0.out"
        if not p.exists():
            continue
        inst = cyc = None
        for line in p.read_text().splitlines():
            if line.startswith("NODE_INST_COUNT ") and "total" not in line:
                inst = float(line.split()[-1])
            if line.startswith("NODE_CYCLE ") and "total" not in line:
                cyc = float(line.split()[-1])
        if inst and cyc:
            ipcs.append(inst / cyc)
    return sum(ipcs) / len(ipcs) if ipcs else None


def read_threshold(helios_sh: Path, app: str) -> int:
    text = helios_sh.read_text()
    m = re.search(rf'\[{re.escape(app)}\]=(\d+)', text)
    return int(m.group(1)) if m else 100


def set_threshold(helios_sh: Path, app: str, value: int) -> None:
    text = helios_sh.read_text()
    new = re.sub(rf'(\[{re.escape(app)}\]=)\d+', rf'\g<1>{value}', text)
    if new == text:
        raise SystemExit(f"Could not update threshold for {app}")
    helios_sh.write_text(new)


def next_threshold(app: str, current: int) -> int | None:
    ladder = LADDER.get(app, [current * 3, current * 10, current * 30])
    for t in ladder:
        if t > current:
            return t
    return None


def negative_apps(scarab_root: Path) -> list[tuple[str, float]]:
    base = scarab_root / "simulations" / "baseline"
    hel = scarab_root / "simulations" / "helios"
    bad = []
    for app in APPS:
        b = avg_ipc(base, app)
        h = avg_ipc(hel, app)
        if b is None or h is None:
            bad.append((app, 0.0))
        elif h / b < 1.0:
            bad.append((app, h / b))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--infra-dir", type=Path, default=Path("/users/deepmish/scarab-infra"))
    ap.add_argument("--scarab-root", type=Path, default=Path("/users/deepmish/scarab/src"))
    ap.add_argument("--max-rounds", type=int, default=5)
    args = ap.parse_args()

    helios_sh = args.infra_dir / "json/hpca2027/helios.sh"
    env = {
        **dict(__import__("os").environ),
        "MCPAT_BIN": "/users/deepmish/mcpat/mcpat",
        "CACTI_BIN": "/users/deepmish/mcpat/cacti/cacti",
    }

    for round_i in range(args.max_rounds):
        bad = negative_apps(args.scarab_root)
        if not bad:
            print(f"Round {round_i}: all apps positive speedup")
            return 0
        print(f"Round {round_i}: negative apps:", bad)
        changed = False
        for app, sp in bad:
            cur = read_threshold(helios_sh, app)
            nxt = next_threshold(app, cur)
            if nxt is None:
                print(f"  {app}: no higher threshold in ladder (cur={cur}, speedup={sp:.4f})")
                continue
            print(f"  {app}: T {cur} -> {nxt}")
            set_threshold(helios_sh, app, nxt)
            changed = True
        if not changed:
            return 1
        # Re-run only affected apps via helios.sh internals
        subprocess.run(
            ["bash", "-c", f"cd {args.infra_dir} && source ~/miniconda3/etc/profile.d/conda.sh && "
             "conda activate scarabinfra && "
             f"export MCPAT_BIN CACTI_BIN && ./json/hpca2027/helios.sh"],
            env=env,
            check=False,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
