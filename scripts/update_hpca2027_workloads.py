#!/usr/bin/env python3
"""Update hpca2027 descriptor JSON files with the ifuse-traces workload list."""

import json
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1]
HPCA_DIR = INFRA / "json" / "hpca2027"

NEW_WORKLOADS = [
    "appworld",
    "bfs-init",
    "bfs-web-google",
    "clickhouse",
    "corebench",
    "dfs-init",
    "dfs-web-google",
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

JSON_FILES = sorted(p for p in HPCA_DIR.glob("*.json"))


def main() -> None:
    for path in JSON_FILES:
        data = json.loads(path.read_text())
        sims = data.get("simulations")
        if not sims:
            continue
        sims[0]["workload"] = list(NEW_WORKLOADS)
        if "warmup" in sims[0]:
            sims[0]["warmup"] = 20_000_000
        path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Updated {path.name}: {len(NEW_WORKLOADS)} workloads")


if __name__ == "__main__":
    main()
