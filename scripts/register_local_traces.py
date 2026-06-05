#!/usr/bin/env python3
"""Register already-downloaded ("raw") simpoint traces into workloads_db.json.

scarab-infra normally produces traces via its tracing flow and records them in
``workloads/workloads_db.json`` with ``finish_trace``. When traces are obtained
out of band (e.g. downloaded into ``traces_dir`` directly), the workloads are
not in the DB and the simulator cannot find them, failing with errors such as
``KeyError: 'bc'`` in ``get_image_list``.

This tool scans a directory of raw simpoint traces and, for each workload:

  * reads ``fingerprint/segment_size`` and ``simpoints/opt.{p,w}.lpt0.99`` (the
    same files ``finish_trace`` consumes) to build the simpoint list,
  * locates the per-cluster trace zips (``traces_simp/trace/<id>.zip`` or
    ``traces_simp/<id>.zip``),
  * creates the layout the simulator expects -- relative symlinks at
    ``<traces_dir>/<suite>/<subsuite>/<workload>/traces/simp/<cluster>.zip`` --
    so no large files are duplicated,
  * writes a ``datacenter``-style entry into ``workloads_db.json``.

The raw trees may be flat (``bfs/...``), nested under a single instance dir
(``bc/bc_web_stanford/...``), or carry macOS AppleDouble (``._*``) junk; all of
these are handled.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_IMAGE_NAME = "allbench_traces"
DEFAULT_TRACE_TYPE = "trace_then_cluster"


def is_junk(name: str) -> bool:
    """macOS AppleDouble sidecar files / resource forks."""
    return name.startswith("._") or name == ".DS_Store" or name == "__MACOSX"


def read_first_line(path: Path) -> Optional[str]:
    try:
        with path.open("r") as f:
            return f.readline().rstrip("\n").strip()
    except OSError:
        return None


def read_weight_file(path: Path) -> Dict[int, float]:
    """opt.w.lpt0.99 lines: '<weight> <segment_id>'."""
    weights: Dict[int, float] = {}
    with path.open("r") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                weights[int(parts[1])] = float(parts[0])
            except ValueError:
                continue
    return weights


def read_cluster_file(path: Path) -> Dict[int, int]:
    """opt.p.lpt0.99 lines: '<cluster_id> <segment_id>' -> {segment_id: cluster_id}."""
    clusters: Dict[int, int] = {}
    with path.open("r") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                clusters[int(parts[1])] = int(parts[0])
            except ValueError:
                continue
    return clusters


def find_trace_root(app_dir: Path) -> Optional[Path]:
    """Return the directory holding simpoints/ (the app dir or a single nested instance dir)."""
    if (app_dir / "simpoints").is_dir():
        return app_dir
    for child in sorted(app_dir.iterdir()):
        if is_junk(child.name) or not child.is_dir():
            continue
        if (child / "simpoints").is_dir():
            return child
    return None


def find_cluster_zips(trace_root: Path) -> Dict[int, Path]:
    """Map cluster_id -> zip path, searching traces_simp/trace then traces_simp."""
    zips: Dict[int, Path] = {}
    for sub in ("traces_simp/trace", "traces_simp"):
        d = trace_root / sub
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if is_junk(entry.name) or entry.suffix != ".zip" or not entry.is_file():
                continue
            stem = entry.stem
            if not stem.isdigit():
                continue
            cid = int(stem)
            zips.setdefault(cid, entry)
        if zips:
            break
    return zips


def build_simpoints(
    weights: Dict[int, float],
    clusters: Dict[int, int],
    zips: Dict[int, Path],
) -> List[dict]:
    """Match weight/cluster files (like finish_trace), keeping only clusters with a zip on disk."""
    simpoints: List[dict] = []
    for segment_id, weight in sorted(weights.items()):
        if segment_id not in clusters:
            continue
        cluster_id = clusters[segment_id]
        if cluster_id not in zips:
            continue
        simpoints.append(
            {"cluster_id": cluster_id, "segment_id": segment_id, "weight": weight}
        )
    # Fallback: no usable simpoint metadata, but zips exist -> register them as
    # single-segment simpoints with equal weight so the workload is still runnable.
    if not simpoints and zips:
        n = len(zips)
        for idx, cid in enumerate(sorted(zips)):
            simpoints.append(
                {"cluster_id": cid, "segment_id": idx, "weight": 1.0 / n}
            )
    return simpoints


def link_traces(
    traces_dir: Path,
    suite: str,
    subsuite: str,
    workload: str,
    simpoints: List[dict],
    zips: Dict[int, Path],
    dry_run: bool,
) -> Path:
    """Create relative symlinks at <traces_dir>/<suite>/<subsuite>/<workload>/traces/simp/<id>.zip."""
    simp_dir = traces_dir / suite / subsuite / workload / "traces" / "simp"
    if not dry_run:
        simp_dir.mkdir(parents=True, exist_ok=True)
        (traces_dir / suite / subsuite / workload / "traces" / "whole").mkdir(
            parents=True, exist_ok=True
        )
    for sp in simpoints:
        cid = sp["cluster_id"]
        target = zips[cid].resolve()
        link = simp_dir / f"{cid}.zip"
        rel = os.path.relpath(target, simp_dir)
        if dry_run:
            continue
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(rel)
    return simp_dir


def register_workload(
    traces_dir: Path,
    app_dir: Path,
    suite: str,
    subsuite: str,
    image_name: str,
    trace_type: str,
    warmup: int,
    dry_run: bool,
) -> Optional[Tuple[str, dict]]:
    workload = app_dir.name
    trace_root = find_trace_root(app_dir)
    if trace_root is None:
        print(f"  [skip] {workload}: no simpoints/ directory found")
        return None

    zips = find_cluster_zips(trace_root)
    if not zips:
        print(f"  [skip] {workload}: no cluster .zip traces found")
        return None

    seg_raw = read_first_line(trace_root / "fingerprint" / "segment_size")
    try:
        segment_size = int(seg_raw)
    except (TypeError, ValueError):
        segment_size = 10000000
        print(f"  [warn] {workload}: missing/invalid segment_size, defaulting to {segment_size}")

    weight_file = trace_root / "simpoints" / "opt.w.lpt0.99"
    cluster_file = trace_root / "simpoints" / "opt.p.lpt0.99"
    weights = read_weight_file(weight_file) if weight_file.is_file() else {}
    clusters = read_cluster_file(cluster_file) if cluster_file.is_file() else {}

    simpoints = build_simpoints(weights, clusters, zips)
    if not simpoints:
        print(f"  [skip] {workload}: could not build any simpoints")
        return None

    link_traces(traces_dir, suite, subsuite, workload, simpoints, zips, dry_run)

    entry = {
        "simulation": {
            "prioritized_mode": "memtrace",
            "memtrace": {
                "image_name": image_name,
                "segment_size": segment_size,
                "warmup": warmup,
                "trace_type": trace_type,
                "whole_trace_file": None,
            },
        },
        "simpoints": simpoints,
    }
    print(
        f"  [ok]   {workload}: {len(simpoints)} simpoint(s), "
        f"segment_size={segment_size}, clusters={sorted(sp['cluster_id'] for sp in simpoints)}"
    )
    return workload, entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--traces-dir", default="/dev/shm/baseline/simpoint_traces",
                        help="Directory containing the raw per-workload trace trees.")
    parser.add_argument("--suite", default="datacenter")
    parser.add_argument("--subsuite", default="datacenter")
    parser.add_argument("--image-name", default=DEFAULT_IMAGE_NAME)
    parser.add_argument("--trace-type", default=DEFAULT_TRACE_TYPE,
                        choices=["trace_then_cluster", "cluster_then_trace", "iterative_trace"])
    parser.add_argument("--warmup", type=int, default=0,
                        help="memtrace.warmup recorded in the DB (descriptor warmup must be <= this).")
    infra_default = str(Path(__file__).resolve().parent.parent / "workloads" / "workloads_db.json")
    parser.add_argument("--workloads-db", default=infra_default,
                        help="Path to workloads_db.json to update.")
    parser.add_argument("--workloads", nargs="*", default=None,
                        help="Optional subset of workload names to register (default: all found).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report, but do not write the DB or create symlinks.")
    args = parser.parse_args()

    traces_dir = Path(args.traces_dir).resolve()
    if not traces_dir.is_dir():
        print(f"ERROR: traces dir not found: {traces_dir}")
        return 1

    db_path = Path(args.workloads_db)
    db: dict = {}
    if db_path.is_file():
        with db_path.open("r") as f:
            db = json.load(f)

    # App dirs are direct children of traces_dir, excluding the suite output tree
    # we generate and any junk/hidden entries.
    app_dirs = []
    for child in sorted(traces_dir.iterdir()):
        if not child.is_dir() or is_junk(child.name) or child.name == args.suite:
            continue
        if args.workloads and child.name not in args.workloads:
            continue
        app_dirs.append(child)

    print(f"Scanning {len(app_dirs)} workload dir(s) under {traces_dir}")
    registered: Dict[str, dict] = {}
    for app_dir in app_dirs:
        result = register_workload(
            traces_dir, app_dir, args.suite, args.subsuite,
            args.image_name, args.trace_type, args.warmup, args.dry_run,
        )
        if result:
            registered[result[0]] = result[1]

    if not registered:
        print("No workloads registered.")
        return 1

    db.setdefault(args.suite, {})
    db[args.suite].setdefault(args.subsuite, {})
    for workload, entry in registered.items():
        db[args.suite][args.subsuite][workload] = entry

    if args.dry_run:
        print(f"\n[dry-run] would register {len(registered)} workload(s) into {db_path}")
        return 0

    with db_path.open("w") as f:
        json.dump(db, f, indent=2, separators=(",", ":"))
    print(f"\nWrote {len(registered)} workload(s) to {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
