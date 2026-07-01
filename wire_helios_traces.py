#!/usr/bin/env python3
"""Wire downloaded HELIOS traces into the `helios_dc` suite layout sci expects.

sci derives the on-disk trace path from `workload_home = suite/subsuite/workload`
(scripts/local_runner.py), so for the `helios_dc` suite it reads:
    <traces_dir>/helios_dc/helios_dc/<app>/traces/simp/<cluster_id>.zip

The HF dataset (harry1332/ifuse-final-datacenter-traces-20260624) downloads as:
    <app>/traces_simp/trace/<id>.zip     (Layout-A apps)
    <app>/traces_simp/<id>.zip           (agentic apps: chemcrow, langchain_web,
                                          rag_haystack, swe_agent, toolformer)

This script HARD-LINKS each app's DB-selected simpoint zips from the download into the suite
layout (hard links = no extra space, and they resolve correctly inside the Docker trace mount,
unlike absolute symlinks; falls back to copy across filesystems). It is idempotent — existing
targets are skipped — so it's safe to run every time.

Driven by json/HELIOS.json (apps + traces_dir) and workloads/workloads_db.json (per-app
simpoint cluster_ids). Auto-called by run_helios.py; also runnable standalone / from setup:
    python3 wire_helios_traces.py [--src <download_dir>] [--traces-dir <dir>]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELIOS = json.loads((HERE / "json" / "HELIOS.json").read_text())
WLDB = json.loads((HERE / "workloads" / "workloads_db.json").read_text())

APPS = [e["app"] for e in HELIOS["helios_optimal"]["per_app_optimal"]]
DEFAULT_TRACES_DIR = Path(HELIOS["runs"][0]["traces_dir"])  # e.g. /dev/shm/baseline
SUITE = SUBSUITE = "helios_dc"


def cluster_ids(app: str) -> list[int]:
    entry = WLDB.get(SUITE, {}).get(SUBSUITE, {}).get(app)
    if not isinstance(entry, dict):
        return []
    return [sp["cluster_id"] for sp in entry.get("simpoints", [])]


def find_src_dir(app: str, src_roots: list[Path]) -> Path | None:
    """Return the dir holding <app>'s trace zips: <root>/<app>/traces_simp[/trace]."""
    for root in src_roots:
        base = root / app / "traces_simp"
        if (base / "trace").is_dir() and any((base / "trace").glob("*.zip")):
            return base / "trace"          # Layout-A
        if base.is_dir() and any(base.glob("*.zip")):
            return base                    # agentic (zips directly under traces_simp)
    return None


def link_one(src: Path, dst: Path) -> str:
    """Hard-link src -> dst (idempotent). Fall back to copy across filesystems."""
    if dst.exists():
        return "skip"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces-dir", type=Path, default=DEFAULT_TRACES_DIR,
                    help=f"suite root (default {DEFAULT_TRACES_DIR})")
    ap.add_argument("--src", type=Path, default=None,
                    help="download root holding <app>/traces_simp (default: auto-detect "
                         "<traces-dir>/new_traces_dl then <traces-dir>)")
    args = ap.parse_args()

    src_roots = [args.src] if args.src else [args.traces_dir / "new_traces_dl", args.traces_dir]
    src_roots = [r for r in src_roots if r and r.is_dir()]

    tally = {"link": 0, "copy": 0, "skip": 0}
    missing_apps, missing_zips = [], []
    for app in APPS:
        cids = cluster_ids(app)
        if not cids:
            print(f"  WARN: no simpoints in workloads_db for {app}", file=sys.stderr)
            continue
        dst_dir = args.traces_dir / SUITE / SUBSUITE / app / "traces" / "simp"
        # Already fully wired? then skip source lookup entirely (download may be gone).
        if all((dst_dir / f"{c}.zip").exists() for c in cids):
            tally["skip"] += len(cids)
            continue
        src_dir = find_src_dir(app, src_roots)
        if src_dir is None:
            missing_apps.append(app)
            continue
        for c in cids:
            src = src_dir / f"{c}.zip"
            if not src.exists():
                missing_zips.append(f"{app}/{c}")
                continue
            tally[link_one(src, dst_dir / f"{c}.zip")] += 1

    print(f"wire_helios_traces: linked={tally['link']} copied={tally['copy']} "
          f"skipped(existing)={tally['skip']}  src_roots={[str(r) for r in src_roots]}")
    if missing_apps:
        print(f"  MISSING download for: {missing_apps} — download the HF dataset first "
              f"(see REPRODUCE.md).", file=sys.stderr)
    if missing_zips:
        print(f"  MISSING simpoint zips: {missing_zips}", file=sys.stderr)
    return 1 if (missing_apps or missing_zips) else 0


if __name__ == "__main__":
    sys.exit(main())
