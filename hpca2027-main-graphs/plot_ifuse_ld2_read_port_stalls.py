#!/usr/bin/env python3
"""L1-D read port stall / L0 bank conflict reduction: before vs after LD2 fuse.

In Scarab, L1-D (dcache / L0) bank conflicts are counted as:
  DCACHE_READ_PORT_UNAVAILABLE_ONPATH_count
from memory.stat.0.csv (on-path only).

  reduction_pct = 100 * (weighted_baseline - weighted_config) / weighted_baseline

Sources:
  baseline:     local simulations/baseline (git fallback: origin/hpca2027-baseline)
  before LD2:   eaea6ca src/simulations/ifuse  (bfs-init / dfs-init / pagerank-init)
  after LD2:    local simulations/ifuse

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ifuse_ld2_read_port_stalls.py
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
from plot_read_port_stalls import (  # noqa: E402
    MEMORY_STAT_FILE,
    READ_PORT_UNAVAILABLE_ONPATH_STAT,
    stat_count_from_csv,
)

BEFORE_COLOR = "#4C78A8"
AFTER_COLOR = IFUSE_COLOR

LD2_SERIES: tuple[tuple[str, str, str], ...] = (
    ("before", "Before fusing LD2", BEFORE_COLOR),
    ("after", "After fusing LD2", AFTER_COLOR),
)

APPS: list[tuple[str, str, str, str]] = [
    ("bfs", "bfs", "bfs-init", "bfs"),
    ("dfs", "dfs", "dfs-init", "dfs"),
    ("pagerank", "pagerank", "pagerank-init", "pagerank"),
    ("core_bench", "core_bench", "corebench", "corebench"),
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
BEFORE_DIR = SIM_ROOT / "ifuse-tt-512"
AFTER_DIR = SIM_ROOT / "ifuse"
BEFORE_GIT_REF = "eaea6ca73d93eb9b56aa3584803723fcb00ee880"
BEFORE_GIT_BASE = "src/simulations/ifuse"
BASELINE_GIT_REF = "origin/hpca2027-baseline"
TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
WORKLOADS_DB = Path("/users/deepmish/scarab-infra/workloads/workloads_db.json")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "ifuse_ld2_read_port_stalls"
NOTO_SERIF_FONT = GRAPH_DIR / "fonts" / "NotoSerif.ttf"


def register_noto_serif() -> None:
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
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def parse_read_port_stalls_text(memory_csv: str) -> float | None:
    for row in csv.reader(memory_csv.splitlines()):
        if len(row) < 3:
            continue
        if row[0].strip() == READ_PORT_UNAVAILABLE_ONPATH_STAT:
            try:
                return float(row[2].strip())
            except ValueError:
                return None
    return None


def stalls_from_sim_dir(
    sim_dir: Path,
    *,
    git_fallback_ref: str | None = None,
    git_rel_path: str | None = None,
) -> float | None:
    local = stat_count_from_csv(sim_dir / MEMORY_STAT_FILE, READ_PORT_UNAVAILABLE_ONPATH_STAT)
    if local is not None:
        return local
    if git_fallback_ref and git_rel_path:
        txt = git_show_text(git_fallback_ref, git_rel_path)
        if txt:
            return parse_read_port_stalls_text(txt)
    return None


def weights_for(app: str, cids: list[str]) -> dict[str, float]:
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


def weighted_stalls(
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
        git_path = (
            f"{git_rel_base}/{app}/{cid}/{MEMORY_STAT_FILE}"
            if git_fallback_ref and git_rel_base
            else None
        )
        if git_only:
            txt = (
                git_show_text(git_fallback_ref, git_path)
                if git_fallback_ref and git_path
                else None
            )
            stalls = parse_read_port_stalls_text(txt) if txt else None
        else:
            stalls = stalls_from_sim_dir(
                exp_dir / app / cid,
                git_fallback_ref=git_fallback_ref,
                git_rel_path=git_path,
            )
        w = wmap.get(cid)
        if stalls is None or w is None:
            continue
        num += w * stalls
        den += w
        used.append(cid)
    if den <= 0:
        return None, used
    return num / den, used


def reduction_pct(baseline: float, config: float) -> float:
    return 100.0 * (baseline - config) / baseline


def compute_rows() -> list[dict]:
    for path, label in ((BASELINE_DIR, "baseline"), (AFTER_DIR, "ifuse")):
        if not path.is_dir():
            raise SystemExit(f"Missing {label} results dir: {path}")

    rows: list[dict] = []
    for key, bapp, oapp, napp in APPS:
        baseline_app = "corebench" if key == "core_bench" else bapp
        b, bu = weighted_stalls(
            BASELINE_DIR,
            baseline_app,
            bapp,
            git_fallback_ref=BASELINE_GIT_REF,
            git_rel_base="src/simulations/baseline",
            git_only=(key == "core_bench"),
        )
        o, ou = weighted_stalls(
            BEFORE_DIR,
            oapp,
            bapp,
            git_fallback_ref=BEFORE_GIT_REF,
            git_rel_base=BEFORE_GIT_BASE,
            git_only=True,
        )
        n, nu = weighted_stalls(AFTER_DIR, napp, bapp)
        if b is None or o is None or n is None or b <= 0:
            raise SystemExit(
                f"Incomplete L0 bank conflicts for {key}: baseline={b} before={o} after={n} "
                f"(sp base={bu} old={ou} new={nu})"
            )
        before_red = reduction_pct(b, o)
        after_red = reduction_pct(b, n)
        rows.append(
            {
                "workload": key,
                "baseline_stalls": b,
                "before_stalls": o,
                "after_stalls": n,
                "before_reduction_pct": round(before_red, 4),
                "after_reduction_pct": round(after_red, 4),
                "before_norm": 1.0 + before_red / 100.0,
                "after_norm": 1.0 + after_red / 100.0,
                "n_simpoints": len(bu),
            }
        )
        print(
            f"{rename_workload(key):14s}  "
            f"base={b:.1f}  before={o:.1f} ({before_red:+.2f}%)  "
            f"after={n:.1f} ({after_red:+.2f}%)"
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
    print("Computing simpoint-weighted L0 bank conflict (read port stall) reductions...")
    print(f"  baseline:     {BASELINE_DIR}")
    print(f"  before:       {BEFORE_GIT_REF}:{BEFORE_GIT_BASE}")
    print(f"  ifuse:        {AFTER_DIR}")
    print(
        f"  stats:        {READ_PORT_UNAVAILABLE_ONPATH_STAT} (on-path only) "
        f"in {MEMORY_STAT_FILE}"
    )
    rows = compute_rows()

    ordered = order_workloads_by_group([r["workload"] for r in rows])
    by_wl = {r["workload"]: r for r in rows}
    rows = [by_wl[wl] for wl in ordered]
    workloads = [r["workload"] for r in rows]

    series_normalized = {
        "before": [r["before_norm"] for r in rows],
        "after": [r["after_norm"] for r in rows],
    }

    mb = sum(r["before_reduction_pct"] for r in rows) / len(rows)
    ma = sum(r["after_reduction_pct"] for r in rows) / len(rows)
    print(f"\nArith mean reduction  before={mb:.2f}%  after={ma:.2f}%")

    summary_path = out / "read_port_stalls_summary.csv"
    with summary_path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display",
                "baseline_stalls",
                "before_stalls",
                "after_stalls",
                "before_reduction_pct",
                "after_reduction_pct",
                "n_simpoints",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "workload": r["workload"],
                    "display": rename_workload(r["workload"]),
                    "baseline_stalls": r["baseline_stalls"],
                    "before_stalls": r["before_stalls"],
                    "after_stalls": r["after_stalls"],
                    "before_reduction_pct": r["before_reduction_pct"],
                    "after_reduction_pct": r["after_reduction_pct"],
                    "n_simpoints": r["n_simpoints"],
                }
            )
        writer.writerow(
            {
                "workload": "arithmetic_mean",
                "display": "Average",
                "baseline_stalls": "",
                "before_stalls": "",
                "after_stalls": "",
                "before_reduction_pct": round(mb, 4),
                "after_reduction_pct": round(ma, 4),
                "n_simpoints": "",
            }
        )
    print(f"Wrote {summary_path}")

    log_path = out / "read_port_stalls_computation_log.txt"
    with log_path.open("w") as fh:
        fh.write(
            "L1-D read port stall / L0 bank conflict reduction "
            f"({READ_PORT_UNAVAILABLE_ONPATH_STAT}, on-path only)\n"
        )
        fh.write("=" * 80 + "\n")
        fh.write(
            "reduction_pct = 100 * (weighted_baseline - weighted_config) "
            "/ weighted_baseline\n\n"
        )
        for r in rows:
            fh.write(f"{r['workload']} ({rename_workload(r['workload'])})\n")
            fh.write(f"  simpoints: {r['n_simpoints']}\n")
            fh.write(f"  weighted baseline stalls: {r['baseline_stalls']:.1f}\n")
            fh.write(
                f"  weighted before stalls:    {r['before_stalls']:.1f}  "
                f"({r['before_reduction_pct']:.2f}% reduction)\n"
            )
            fh.write(
                f"  weighted after stalls:     {r['after_stalls']:.1f}  "
                f"({r['after_reduction_pct']:.2f}% reduction)\n\n"
            )
        fh.write(f"Arithmetic mean before reduction: {mb:.2f}%\n")
        fh.write(f"Arithmetic mean after reduction:  {ma:.2f}%\n")
    print(f"Wrote {log_path}")

    meta = {
        "baseline_dir": str(BASELINE_DIR),
        "before_git_ref": BEFORE_GIT_REF,
        "before_git_base": BEFORE_GIT_BASE,
        "after_dir": str(AFTER_DIR),
        "metric": "DCACHE_READ_PORT_UNAVAILABLE_ONPATH (L0 bank conflicts, on-path only)",
        "mean_before_reduction_pct": mb,
        "mean_after_reduction_pct": ma,
        "rows": rows,
    }
    (out / "read_port_stalls_meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    plot_speedup_bars(
        workloads,
        series_normalized,
        out,
        series=LD2_SERIES,
        figure_height=10.0,
        legend_outside=True,
        ylabel="L1-D cache read port stalls reduction (%)\n(normalized to no-fusion)",
        file_prefix="read_port_stalls",
    )
    print("\nPlots saved:")
    for stem in ("read_port_stalls-labeled", "read_port_stalls"):
        print(f"  - {out / f'{stem}.png'}")
        print(f"  - {out / f'{stem}.pdf'}")


if __name__ == "__main__":
    main()
