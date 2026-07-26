#!/usr/bin/env python3
"""Package per-app tuned RFP results into scarab/src/rfp-final-results/."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

SCARAB_SRC = Path("/users/deepmish/scarab/src")
SIM_ROOT = SCARAB_SRC / "simulations"
BASELINE_ROOT = SCARAB_SRC / "simulations-confidence-1" / "baseline"
OUT = SCARAB_SRC / "rfp-final-results"

COMMON = (
    "--rfp_pt_num_sets 512 --rfp_pt_num_ways 8 "
    "--rfp_pat_num_sets 64 --rfp_pat_num_ways 4"
)

# Per-app best config: graph apps use 16b signed stride tuning; others use prob_shift=2.
APP_CONFIG: dict[str, dict] = {
    "bfs": {
        "config": "rfp_p3_s16",
        "prob_shift": 3,
        "stride_bits": 16,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "dfs": {
        "config": "rfp_p0_s16",
        "prob_shift": 0,
        "stride_bits": 16,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "pagerank": {
        "config": "rfp_p1_s16",
        "prob_shift": 1,
        "stride_bits": 16,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "appworld": {
        "config": "rfp_prob_p2",
        "prob_shift": 2,
        "stride_bits": 5,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "clickhouse": {
        "config": "rfp_prob_p2",
        "prob_shift": 2,
        "stride_bits": 5,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "core_bench": {
        "config": "rfp_prob_p2",
        "prob_shift": 2,
        "stride_bits": 5,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "duckdb": {
        "config": "rfp_prob_p2",
        "prob_shift": 2,
        "stride_bits": 5,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "rocksdb": {
        "config": "rfp_prob_p2",
        "prob_shift": 2,
        "stride_bits": 5,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
    "terminal_bench": {
        "config": "rfp_prob_p2",
        "prob_shift": 2,
        "stride_bits": 5,
        "stride_signed": 1,
        "sim_root": SIM_ROOT,
    },
}

APPS = list(APP_CONFIG.keys())


def _params(entry: dict) -> str:
    return (
        f"--rfp_prob_shift {entry['prob_shift']} "
        f"--rfp_conf_max 1 "
        f"--rfp_stride_signed {entry['stride_signed']} "
        f"--rfp_stride_bits {entry['stride_bits']} "
        f"{COMMON}"
    )


def _simpoint_dirs(root: Path, wl: str) -> list[tuple[str, Path]]:
    wl_dir = root / wl
    if not wl_dir.is_dir():
        return []
    out = []
    for cid in sorted(wl_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else p.name):
        if cid.is_dir():
            out.append((cid.name, cid))
    return out


def _symlink_tree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink(missing_ok=True)
    os.symlink(src.resolve(), dst)


def main() -> None:
    if OUT.exists():
        import shutil

        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    configs_out: dict = {"apps": {}, "baseline": "simulations-confidence-1/baseline"}
    summary_rows: list[dict] = []

    for wl in APPS:
        entry = APP_CONFIG[wl]
        cfg = entry["config"]
        sim_root = entry["sim_root"]
        app_dir = OUT / wl
        baseline_dir = app_dir / "baseline"
        rfp_dir = app_dir / "rfp"

        bl_sps = _simpoint_dirs(BASELINE_ROOT, wl)
        rfp_sps = _simpoint_dirs(sim_root / cfg, wl)

        if not bl_sps:
            print(f"WARN: no baseline simpoints for {wl}")
        if not rfp_sps:
            print(f"WARN: no RFP simpoints for {wl} ({cfg})")

        for cid, src in bl_sps:
            _symlink_tree(src, baseline_dir / cid)
        for cid, src in rfp_sps:
            _symlink_tree(src, rfp_dir / cid)

        configs_out["apps"][wl] = {
            "config": cfg,
            "prob_shift": entry["prob_shift"],
            "rfp_conf_max": 1,
            "rfp_stride_bits": entry["stride_bits"],
            "rfp_stride_signed": entry["stride_signed"],
            "params": _params(entry),
            "sim_root": str(sim_root.relative_to(SCARAB_SRC)),
            "baseline_simpoints": len(bl_sps),
            "rfp_simpoints": len(rfp_sps),
        }
        summary_rows.append(
            {
                "workload": wl,
                "config": cfg,
                "prob_shift": entry["prob_shift"],
                "stride_bits": entry["stride_bits"],
                "baseline_sps": len(bl_sps),
                "rfp_sps": len(rfp_sps),
            }
        )

    with open(OUT / "configs.json", "w") as f:
        json.dump(configs_out, f, indent=2)

    with open(OUT / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "workload",
                "config",
                "prob_shift",
                "stride_bits",
                "baseline_sps",
                "rfp_sps",
            ],
        )
        w.writeheader()
        w.writerows(summary_rows)

    readme = """# RFP Final Results Package

Per-app tuned RFP prefetcher results (9 workloads). Each app folder contains
`baseline/` and `rfp/` simpoint symlinks into the source simulation trees.

## Layout

```
rfp-final-results/
  <app>/
    baseline/<simpoint_id>/   -> simulations-confidence-1/baseline/<app>/<id>
    rfp/<simpoint_id>/        -> per-app best RFP config (see below)
  configs.json
  summary.csv
```

## Baseline

All workloads use `simulations-confidence-1/baseline/<app>/<simpoint_id>/`.

## Per-app RFP configs

| App | Config | prob_shift | stride_bits | stride_signed | conf_max |
|-----|--------|------------|-------------|---------------|----------|
| bfs | rfp_p3_s16 | 3 | 16 | signed (1) | 1 |
| dfs | rfp_p0_s16 | 0 | 16 | signed (1) | 1 |
| pagerank | rfp_p1_s16 | 1 | 16 | signed (1) | 1 |
| appworld | rfp_prob_p2 | 2 | 5 | signed (1) | 1 |
| clickhouse | rfp_prob_p2 | 2 | 5 | signed (1) | 1 |
| core_bench | rfp_prob_p2 | 2 | 5 | signed (1) | 1 |
| duckdb | rfp_prob_p2 | 2 | 5 | signed (1) | 1 |
| rocksdb | rfp_prob_p2 | 2 | 5 | signed (1) | 1 |
| terminal_bench | rfp_prob_p2 | 2 | 5 | signed (1) | 1 |

Graph workloads (bfs, dfs, pagerank) use 16-bit signed stride after per-app tuning
(`rfp-graph-stride` / `rfp-graph-stride2` sweeps). Other apps use the original
`rfp_prob_p2` config from the confidence-1 storage sweep.

## Common Scarab flags (all apps)

```
--rfp_pt_num_sets 512 --rfp_pt_num_ways 8 --rfp_pat_num_sets 64 --rfp_pat_num_ways 4
```

## Full CLI example (BFS)

```
--rfp_prob_shift 3 --rfp_conf_max 1 --rfp_stride_signed 1 --rfp_stride_bits 16 \\
--rfp_pt_num_sets 512 --rfp_pt_num_ways 8 --rfp_pat_num_sets 64 --rfp_pat_num_ways 4
```

See `configs.json` for per-app `params` strings and simpoint counts.
"""
    (OUT / "README.md").write_text(readme)

    print(f"Packaged {len(APPS)} apps -> {OUT}")
    for row in summary_rows:
        print(
            f"  {row['workload']:16s} {row['config']:14s} "
            f"p{row['prob_shift']} s{row['stride_bits']}b  "
            f"bl={row['baseline_sps']} rfp={row['rfp_sps']}"
        )


if __name__ == "__main__":
    main()
