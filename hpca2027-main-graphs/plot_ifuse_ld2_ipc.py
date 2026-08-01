#!/usr/bin/env python3
"""IPC speedup vs no-fusion baseline: old I-Fuse vs new I-Fuse.

Uses the same bar styling as plot_ipc.py (grouped apps, Average column,
Noto Serif, labeled/unlabeled PNG+PDF).

Reads local trees under scarab/src/simulations/:
  baseline/              no-fusion (dcache_assoc=12)
  ifuse-old/             old I-Fuse (bfs-init / dfs-init / pagerank-init)
  ifuse/                 new I-Fuse (dcache_assoc=12)

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ifuse_ld2_ipc.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

import plot_ipc  # noqa: E402
from plot_ipc import (  # noqa: E402
    DEFAULT_RESULTS_ROOT,
    IFUSE_COLOR,
    order_workloads_by_group,
    plot_speedup_bars,
    rename_workload,
)

# Use clearly distinct colors for the side-by-side comparison.
BEFORE_COLOR = "#4C78A8"
AFTER_COLOR = IFUSE_COLOR

LD2_SERIES: tuple[tuple[str, str, str], ...] = (
    ("before", "Old I-Fuse", BEFORE_COLOR),
    ("after", "New I-Fuse", AFTER_COLOR),
)

# Keys match plot_ipc WORKLOAD_GROUPS / rename_workload (core_bench not corebench).
APPS: list[tuple[str, str, str, str]] = [
    # label_key, baseline_app, old_ifuse_app, new_ifuse_app
    ("bfs", "bfs", "bfs-init", "bfs"),
    ("dfs", "dfs", "dfs-init", "dfs"),
    ("pagerank", "pagerank", "pagerank-init", "pagerank"),
    ("core_bench", "corebench", "corebench", "corebench"),
    ("appworld", "appworld", "appworld", "appworld"),
    ("terminal_bench", "terminal_bench", "terminal_bench", "terminal_bench"),
    ("clickhouse", "clickhouse", "clickhouse", "clickhouse"),
    ("duckdb", "duckdb", "duckdb", "duckdb"),
    ("leveldb", "leveldb", "leveldb", "leveldb"),
    ("memcached", "memcached", "memcached", "memcached"),
]

SCARAB = Path("/users/deepmish/scarab")
SIM_ROOT = SCARAB / "src" / "simulations"
BASELINE_DIR = SIM_ROOT / "baseline"
BEFORE_DIR = SIM_ROOT / "ifuse-old"
AFTER_DIR = SIM_ROOT / "ifuse"
BEFORE_GIT_REF = "eaea6ca73d93eb9b56aa3584803723fcb00ee880"
BEFORE_GIT_BASE = "src/simulations/ifuse"
BASELINE_GIT_REF = "origin/hpca2027-baseline"
TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
WORKLOADS_DB = Path("/users/deepmish/scarab-infra/workloads/workloads_db.json")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "ifuse_old_vs_new_ipc"
NOTO_SERIF_FONT = GRAPH_DIR / "fonts" / "NotoSerif.ttf"


def register_noto_serif() -> None:
    """Register the bundled Noto Serif font for all plot_ipc text."""
    if not NOTO_SERIF_FONT.is_file():
        raise SystemExit(f"Missing required Noto Serif font: {NOTO_SERIF_FONT}")
    from matplotlib import font_manager

    font_manager.fontManager.addfont(str(NOTO_SERIF_FONT))
    font_name = font_manager.FontProperties(fname=str(NOTO_SERIF_FONT)).get_name()
    plot_ipc.FONT_FAMILY = font_name
    print(f"Using font: {font_name} ({NOTO_SERIF_FONT})")


def list_cids(exp_dir: Path, app: str) -> list[str]:
    app_dir = exp_dir / app
    if not app_dir.is_dir():
        return []
    return sorted(
        (p.name for p in app_dir.iterdir() if p.is_dir() and p.name.isdigit()),
        key=lambda x: int(x),
    )


def list_cids_git(ref: str, rel_base: str, app: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(SCARAB), "ls-tree", "--name-only", f"{ref}:{rel_base}/{app}"],
            text=True,
            errors="replace",
        )
    except subprocess.CalledProcessError:
        return []
    return sorted((x for x in out.splitlines() if x.isdigit()), key=int)


def git_show_text(ref: str, path: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(SCARAB), "show", f"{ref}:{path}"],
            text=True,
            errors="replace",
        )
    except subprocess.CalledProcessError:
        return None


def parse_periodic_ipc(core_stat_csv: str) -> float | None:
    pin = pcy = None
    for row in csv.reader(core_stat_csv.splitlines()):
        if len(row) < 3:
            continue
        stat = row[0].strip()
        if stat == "Periodic_Instructions":
            pin = float(row[2].strip())
        elif stat == "Periodic_Cycles":
            pcy = float(row[2].strip())
    if pin is None or pcy is None or pcy <= 0:
        return None
    return pin / pcy


def ipc_from_sim_dir(sim_dir: Path) -> float | None:
    core_stat = sim_dir / "core.stat.0.csv"
    if not core_stat.is_file():
        return None
    return parse_periodic_ipc(core_stat.read_text(errors="replace"))


def weights_for(app: str, cids: list[str]) -> dict[str, float]:
    """Prefer opt.p/opt.w when cluster IDs match sims; else workloads_db."""
    root = TRACE_ROOT / app
    if root.is_dir() and not (root / "simpoints").is_dir():
        for child in root.iterdir():
            if child.is_dir() and (child / "simpoints").is_dir():
                root = child
                break
    pfile = root / "simpoints" / "opt.p"
    wfile = root / "simpoints" / "opt.w"
    cl_w: dict[str, float] = {}
    if pfile.is_file() and wfile.is_file():
        seg_w: dict[str, float] = {}
        with wfile.open() as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2:
                    seg_w[parts[1]] = float(parts[0])
        with pfile.open() as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2 and parts[1] in seg_w:
                    cl_w[parts[0]] = seg_w[parts[1]]
    if any(c in cl_w for c in cids):
        return cl_w
    db = json.loads(WORKLOADS_DB.read_text())["datacenter"]["datacenter"]
    return {
        str(sp["cluster_id"]): float(sp["weight"])
        for sp in db.get(app, {}).get("simpoints", [])
    }


def weighted_ipc(
    exp_dir: Path,
    app: str,
    weight_app: str,
    *,
    git_fallback_ref: str | None = None,
    git_rel_base: str | None = None,
    git_only: bool = False,
) -> tuple[float | None, list[str]]:
    cids = [] if git_only else list_cids(exp_dir, app)
    if (git_only or not cids) and git_fallback_ref and git_rel_base:
        cids = list_cids_git(git_fallback_ref, git_rel_base, app)
    if not cids:
        return None, []
    wmap = weights_for(weight_app, cids)
    num = den = 0.0
    used: list[str] = []
    for cid in cids:
        ipc = None if git_only else ipc_from_sim_dir(exp_dir / app / cid)
        if (git_only or ipc is None) and git_fallback_ref and git_rel_base:
            txt = git_show_text(
                git_fallback_ref, f"{git_rel_base}/{app}/{cid}/core.stat.0.csv"
            )
            if txt:
                ipc = parse_periodic_ipc(txt)
        w = wmap.get(cid)
        if ipc is None or w is None:
            continue
        num += w * ipc
        den += w
        used.append(cid)
    if den <= 0:
        return None, used
    return num / den, used


def compute_rows() -> list[dict]:
    for path, label in (
        (BASELINE_DIR, "baseline"),
        (BEFORE_DIR, "ifuse-old"),
        (AFTER_DIR, "ifuse"),
    ):
        if not path.is_dir():
            raise SystemExit(f"Missing {label} results dir: {path}")

    rows: list[dict] = []
    for key, bapp, oapp, napp in APPS:
        b, bu = weighted_ipc(BASELINE_DIR, bapp, bapp)
        o, ou = weighted_ipc(BEFORE_DIR, oapp, bapp)
        n, nu = weighted_ipc(AFTER_DIR, napp, bapp)
        if b is None or o is None or n is None:
            raise SystemExit(
                f"Incomplete IPC for {key}: baseline={b} before={o} after={n} "
                f"(sp base={bu} old={ou} new={nu})"
            )
        rows.append(
            {
                "workload": key,
                "baseline_ipc": b,
                "before_ipc": o,
                "after_ipc": n,
                "before_norm": round(o / b, 4),
                "after_norm": round(n / b, 4),
                "n_simpoints": len(bu),
            }
        )
        print(
            f"{rename_workload(key):14s}  "
            f"base={b:.4f}  old={o:.4f} ({o/b:.4f})  new={n:.4f} ({n/b:.4f})"
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Plot output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()
    out: Path = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    register_noto_serif()
    print("Computing simpoint-weighted Periodic IPC from local simulations/...")
    print(f"  baseline:     {BASELINE_DIR}  (DCACHE_ASSOC=12)")
    print(f"  old ifuse:    {BEFORE_DIR}  (DCACHE_ASSOC=12)")
    print(f"  new ifuse:    {AFTER_DIR}  (DCACHE_ASSOC=12)")
    rows = compute_rows()
    workloads = [r["workload"] for r in rows]
    ordered = order_workloads_by_group(workloads)
    by_wl = {r["workload"]: r for r in rows}
    rows = [by_wl[wl] for wl in ordered]
    workloads = [r["workload"] for r in rows]

    series_normalized = {
        "before": [r["before_norm"] for r in rows],
        "after": [r["after_norm"] for r in rows],
    }

    def geomean(vals: list[float]) -> float:
        return math.exp(sum(math.log(v) for v in vals) / len(vals))

    gb = geomean(series_normalized["before"])
    ga = geomean(series_normalized["after"])
    mb = sum(series_normalized["before"]) / len(rows)
    ma = sum(series_normalized["after"]) / len(rows)
    print(f"\nGeomean  old={gb:.4f} ({(gb-1)*100:+.2f}%)  new={ga:.4f} ({(ga-1)*100:+.2f}%)")
    print(f"Arith mean old={mb:.4f}  new={ma:.4f}")

    summary_path = out / "ipc_summary.csv"
    with summary_path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display",
                "baseline_ipc",
                "before_ipc",
                "after_ipc",
                "before_norm",
                "after_norm",
                "n_simpoints",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, "display": rename_workload(r["workload"])})
        writer.writerow(
            {
                "workload": "arithmetic_mean",
                "display": "Average",
                "baseline_ipc": "",
                "before_ipc": "",
                "after_ipc": "",
                "before_norm": round(mb, 4),
                "after_norm": round(ma, 4),
                "n_simpoints": "",
            }
        )
        writer.writerow(
            {
                "workload": "geomean",
                "display": "Geomean",
                "baseline_ipc": "",
                "before_ipc": "",
                "after_ipc": "",
                "before_norm": round(gb, 4),
                "after_norm": round(ga, 4),
                "n_simpoints": "",
            }
        )
    print(f"Wrote {summary_path}")

    meta = {
        "baseline_dir": str(BASELINE_DIR),
        "before_dir": str(BEFORE_DIR),
        "before_git_ref": BEFORE_GIT_REF,
        "after_dir": str(AFTER_DIR),
        "geomean_before": gb,
        "geomean_after": ga,
        "mean_before": mb,
        "mean_after": ma,
        "rows": rows,
    }
    (out / "ipc_meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    plot_speedup_bars(
        workloads,
        series_normalized,
        out,
        series=LD2_SERIES,
    )
    print("\nPlots saved:")
    for stem in ("ipc-labeled", "ipc"):
        print(f"  - {out / f'{stem}.png'}")
        print(f"  - {out / f'{stem}.pdf'}")


if __name__ == "__main__":
    main()
