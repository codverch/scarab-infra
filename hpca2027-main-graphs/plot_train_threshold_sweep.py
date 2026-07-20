#!/usr/bin/env python3
"""Plot runtime I-Fuse train-threshold sweep vs hpca2027 no-fusion baseline.

Reads completed simpoints only (skips missing core.stat.0.out).
Speedups are vs simulations/baseline (hpca2027/baseline), not vs another threshold.

Example:
  ~/miniconda3/envs/scarabinfra/bin/python \\
    ~/scarab-infra/hpca2027-main-graphs/plot_train_threshold_sweep.py
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DEFAULT_ROOT = Path(
    "/users/deepmish/scarab/src/simulations/runtime-ifuse-train-threshold-sweep"
)
DEFAULT_BASELINE = Path("/users/deepmish/scarab/src/simulations/baseline")
DEFAULT_CONFIGS = [
    "train_thresh_10",
    "train_thresh_100",
    "train_thresh_1000",
    "train_thresh_10000",
]
TT64_CONFIGS = [
    "tt64_thresh_10",
    "tt64_thresh_100",
    "tt64_thresh_1000",
    "tt64_thresh_10000",
]


def thresh_label(cfg: str) -> str:
    return cfg.rsplit("_", 1)[-1]
# Only alias when names differ but sims are the same workload/simpoints.
# Do NOT map community↔cd or connected_components↔cc (different cluster IDs).
BASELINE_APP_ALIAS: dict[str, str] = {}
DISPLAY = {
    "appworld": "AppWorld",
    "bc": "BC",
    "bfs": "BFS",
    "community": "Community*",
    "connected_components": "CC*",
    "core_bench": "CoreBench",
    "dfs": "DFS",
    "duckdb": "DuckDB",
    "leveldb": "LevelDB",
    "mlgym_fmnist": "MLGym",
    "pagerank": "PR",
    "rocksdb": "RocksDB",
    "sssp_ego_fb": "SSSP",
    "terminal_bench": "TermBench",
}
APP_ORDER = [
    "bc",
    "bfs",
    "dfs",
    "pagerank",
    "sssp_ego_fb",
    "community",
    "connected_components",
    "duckdb",
    "leveldb",
    "rocksdb",
    "appworld",
    "core_bench",
    "terminal_bench",
    "mlgym_fmnist",
]


def read_ipc(path: Path) -> float | None:
    out = path / "core.stat.0.out"
    if out.is_file():
        with out.open() as fh:
            for line in fh:
                if line.startswith("Cumulative:") and "IPC:" in line:
                    m = re.search(r"IPC:\s*([0-9.]+)", line)
                    return float(m.group(1)) if m else None
    csv_path = path / "core.stat.0.csv"
    if csv_path.is_file():
        with csv_path.open(newline="") as fh:
            for row in csv.reader(fh):
                if len(row) >= 2 and row[0].strip() in {"IPC", "Cumulative_IPC"}:
                    try:
                        return float(row[-1])
                    except ValueError:
                        continue
    return None


def read_coverage_pct(path: Path) -> float | None:
    csv_path = path / "ifuse.stat.0.csv"
    if not csv_path.is_file():
        return None
    best = None
    with csv_path.open(newline="") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            name = row[0]
            if name in {"IFUSE_LOAD_COVERAGE_pct", "IFUSE_LOAD_COVERAGE_total_pct"}:
                for cell in reversed(row):
                    try:
                        val = float(cell)
                        best = val
                        if "total" in name:
                            return val * 100.0
                        break
                    except ValueError:
                        continue
    return None if best is None else best * 100.0


def resolve_app_root(experiment_root: Path, config: str | None, app: str) -> Path | None:
    """Find .../datacenter/datacenter/<app> or flattened <app> under experiment_root."""
    candidates: list[Path] = []
    if config:
        candidates += [
            experiment_root / config / "datacenter" / "datacenter" / app,
            experiment_root / config / app,
        ]
    else:
        # nested: baseline/baseline/datacenter/datacenter/<app>
        # flat:    baseline/<app>
        candidates += [
            experiment_root / "baseline" / "datacenter" / "datacenter" / app,
            experiment_root / "datacenter" / "datacenter" / app,
            experiment_root / app,
        ]
    for p in candidates:
        if p.is_dir():
            return p
    return None


def collect_simpoint_ipcs(app_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for sp_dir in sorted(app_dir.iterdir()):
        if not sp_dir.is_dir():
            continue
        ipc = read_ipc(sp_dir)
        if ipc is not None:
            out[sp_dir.name] = ipc
    return out


def collect_simpoint_cov(app_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for sp_dir in sorted(app_dir.iterdir()):
        if not sp_dir.is_dir():
            continue
        cov = read_coverage_pct(sp_dir)
        if cov is not None:
            out[sp_dir.name] = cov
    return out


def baseline_app_name(app: str) -> str:
    return BASELINE_APP_ALIAS.get(app, app)


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def detect_configs(sweep_root: Path) -> list[str]:
    present = {p.name for p in sweep_root.iterdir() if p.is_dir()}
    for candidates in (TT64_CONFIGS, DEFAULT_CONFIGS):
        found = [c for c in candidates if c in present]
        if found:
            return found
    # Fallback: any *thresh_* dirs
    return sorted(
        n
        for n in present
        if "thresh" in n and (sweep_root / n).is_dir()
    )


def discover_apps(sweep_root: Path, configs: list[str]) -> list[str]:
    apps_set: set[str] = set()
    skip = {"datacenter", "logs", "hpca2027-plots"}
    for cfg in configs:
        for candidate in (
            sweep_root / cfg / "datacenter" / "datacenter",
            sweep_root / cfg,
        ):
            if not candidate.is_dir():
                continue
            for p in candidate.iterdir():
                if p.is_dir() and p.name not in skip and not p.name.startswith("."):
                    # only count dirs that look like apps (have simpoint subdirs)
                    if any(c.is_dir() for c in p.iterdir()):
                        apps_set.add(p.name)
    return [a for a in APP_ORDER if a in apps_set] + sorted(apps_set - set(APP_ORDER))


def collect_all(
    sweep_root: Path,
    baseline_root: Path,
    configs: list[str],
    complete_only: bool = False,
    app_filter: list[str] | None = None,
    allow_unmatched_baseline: bool = False,
) -> tuple[dict, dict, list[str]]:
    """
    Returns:
      sweep[cfg][app] = {ipc, cov, n_ok, n_tot, n_matched}
      baseline[app] = {ipc, n_ok}
      apps list
    """
    apps = discover_apps(sweep_root, configs)
    if app_filter:
        want = set(app_filter)
        apps = [a for a in apps if a in want]
        missing = sorted(want - set(apps))
        if missing:
            print(f"WARN: requested apps not found in sweep: {', '.join(missing)}")

    baseline: dict[str, dict] = {}
    for app in apps:
        bname = baseline_app_name(app)
        bdir = resolve_app_root(baseline_root, None, bname)
        if bdir is None:
            continue
        ipcs = collect_simpoint_ipcs(bdir)
        if not ipcs:
            continue
        baseline[app] = {
            "ipc_by_sp": ipcs,
            "ipc": mean(list(ipcs.values())),
            "n_ok": len(ipcs),
        }

    sweep: dict[str, dict[str, dict]] = {}
    for cfg in configs:
        sweep[cfg] = {}
        for app in apps:
            adir = resolve_app_root(sweep_root, cfg, app)
            if adir is None:
                continue
            ipcs = collect_simpoint_ipcs(adir)
            covs = collect_simpoint_cov(adir)
            n_tot = sum(1 for p in adir.iterdir() if p.is_dir())
            if not ipcs:
                # Still record totals so --complete-only can exclude
                sweep[cfg][app] = {
                    "ipc": None,
                    "baseline_ipc": None,
                    "cov": None,
                    "n_ok": 0,
                    "n_tot": n_tot,
                    "n_matched": 0,
                    "speedup_pct": None,
                }
                continue
            # Prefer simpoints shared with baseline; optionally fall back to
            # mean(sweep) vs mean(baseline) when IDs do not overlap.
            bipcs = baseline.get(app, {}).get("ipc_by_sp", {})
            common = sorted(set(ipcs) & set(bipcs)) if bipcs else []
            if common:
                avg_ipc = mean([ipcs[s] for s in common])
                avg_base = mean([bipcs[s] for s in common])
                n_matched = len(common)
                cov_keys = common
            else:
                avg_ipc = mean(list(ipcs.values()))
                avg_base = (
                    baseline.get(app, {}).get("ipc")
                    if allow_unmatched_baseline
                    else None
                )
                n_matched = 0
                cov_keys = sorted(ipcs)
            cov_vals = [covs[s] for s in cov_keys if s in covs]
            sweep[cfg][app] = {
                "ipc": avg_ipc,
                "baseline_ipc": avg_base,
                "cov": mean(cov_vals),
                "n_ok": len(ipcs),
                "n_tot": n_tot,
                "n_matched": n_matched,
                "speedup_pct": (
                    (avg_ipc / avg_base - 1.0) * 100.0
                    if avg_ipc is not None and avg_base
                    else None
                ),
            }

    if complete_only:
        apps = [
            a
            for a in apps
            if all(
                (row := sweep.get(c, {}).get(a))
                and row["n_ok"] > 0
                and row["n_ok"] == row["n_tot"]
                for c in configs
            )
        ]

    return sweep, baseline, apps


def threshold_fully_done(row: dict | None) -> bool:
    return bool(row and row.get("n_ok", 0) > 0 and row["n_ok"] == row["n_tot"])


def mask_incomplete_thresholds(
    sweep: dict, configs: list[str], apps: list[str]
) -> dict:
    """Zero-out metrics for (cfg, app) where any simpoint is missing."""
    out: dict = {}
    for cfg in configs:
        out[cfg] = {}
        for app in apps:
            row = sweep.get(cfg, {}).get(app)
            if row is None:
                continue
            if threshold_fully_done(row):
                out[cfg][app] = row
            else:
                out[cfg][app] = {
                    **row,
                    "ipc": None,
                    "baseline_ipc": None,
                    "cov": None,
                    "speedup_pct": None,
                }
    return out


def plot_speedup_vs_baseline(
    sweep: dict, apps: list[str], configs: list[str], out: Path, title: str
) -> list[str]:
    """Plot all thresholds vs no-fusion baseline. Returns apps actually plotted."""
    plot_apps = [
        a
        for a in apps
        if any(sweep.get(c, {}).get(a, {}).get("speedup_pct") is not None for c in configs)
    ]
    if not plot_apps:
        print("WARN: no apps with baseline-matched speedups; skip speedup plot")
        return []

    x = np.arange(len(plot_apps))
    width = 0.8 / len(configs)
    fig, ax = plt.subplots(figsize=(12, 4.8))
    for i, cfg in enumerate(configs):
        vals = [
            sweep.get(cfg, {}).get(a, {}).get("speedup_pct", float("nan"))
            if sweep.get(cfg, {}).get(a, {}).get("speedup_pct") is not None
            else float("nan")
            for a in plot_apps
        ]
        vals = [float("nan") if v is None else v for v in vals]
        offset = (i - (len(configs) - 1) / 2) * width
        ax.bar(x + offset, vals, width, label=f"thresh {thresh_label(cfg)}")
    ax.axhline(0.0, color="#666666", linewidth=0.8, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY.get(a, a) for a in plot_apps], rotation=35, ha="right")
    ax.set_ylabel("IPC speedup vs baseline (%)")
    ax.set_title(title)
    ax.legend(frameon=False, ncols=4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)
    print(f"Wrote {out}")
    return plot_apps


def plot_absolute_ipc(
    sweep: dict, baseline: dict, apps: list[str], configs: list[str], out: Path, title: str
) -> None:
    series = ["baseline"] + configs
    labels = ["baseline"] + [thresh_label(c) for c in configs]
    x = np.arange(len(apps))
    width = 0.8 / len(series)
    fig, ax = plt.subplots(figsize=(12, 4.8))
    for i, (key, lab) in enumerate(zip(series, labels)):
        if key == "baseline":
            vals = [
                baseline.get(a, {}).get("ipc", float("nan"))
                if baseline.get(a, {}).get("ipc") is not None
                else float("nan")
                for a in apps
            ]
        else:
            vals = [
                sweep.get(key, {}).get(a, {}).get("ipc", float("nan"))
                if sweep.get(key, {}).get(a, {}).get("ipc") is not None
                else float("nan")
                for a in apps
            ]
        offset = (i - (len(series) - 1) / 2) * width
        ax.bar(x + offset, vals, width, label=lab)
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY.get(a, a) for a in apps], rotation=35, ha="right")
    ax.set_ylabel("IPC")
    ax.set_title(title)
    ax.legend(frameon=False, ncols=5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)
    print(f"Wrote {out}")


def plot_coverage(
    sweep: dict, apps: list[str], configs: list[str], out: Path, title: str
) -> None:
    x = np.arange(len(apps))
    width = 0.8 / len(configs)
    fig, ax = plt.subplots(figsize=(12, 4.5))
    for i, cfg in enumerate(configs):
        vals = []
        for a in apps:
            v = sweep.get(cfg, {}).get(a, {}).get("cov")
            vals.append(float("nan") if v is None else v)
        offset = (i - (len(configs) - 1) / 2) * width
        ax.bar(x + offset, vals, width, label=f"thresh {thresh_label(cfg)}")
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY.get(a, a) for a in apps], rotation=35, ha="right")
    ax.set_ylabel("Load coverage (%)")
    ax.set_title(title)
    ax.legend(frameon=False, ncols=4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)
    print(f"Wrote {out}")


def write_summary_csv(
    sweep: dict, baseline: dict, apps: list[str], configs: list[str], out: Path
) -> None:
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "app",
                "config",
                "avg_ipc",
                "baseline_ipc_matched",
                "ipc_speedup_vs_baseline_pct",
                "coverage_pct",
                "n_ok",
                "n_tot",
                "n_matched_baseline",
            ]
        )
        for app in apps:
            for cfg in configs:
                row = sweep.get(cfg, {}).get(app)
                if not row or row.get("ipc") is None:
                    continue
                sp = row.get("speedup_pct")
                cov = row.get("cov")
                w.writerow(
                    [
                        app,
                        cfg,
                        f"{row['ipc']:.6f}" if row.get("ipc") is not None else "",
                        (
                            f"{row['baseline_ipc']:.6f}"
                            if row.get("baseline_ipc") is not None
                            else ""
                        ),
                        f"{sp:.4f}" if sp is not None else "",
                        f"{cov:.4f}" if cov is not None else "",
                        int(row["n_ok"]),
                        int(row["n_tot"]),
                        int(row["n_matched"]),
                    ]
                )
    print(f"Wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument(
        "--baseline-dir",
        type=Path,
        default=DEFAULT_BASELINE,
        help="hpca2027/baseline results (default: simulations/baseline)",
    )
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument(
        "--configs",
        nargs="+",
        default=None,
        help="Config dir names (default: auto-detect tt64_thresh_* or train_thresh_*)",
    )
    p.add_argument(
        "--complete-only",
        action="store_true",
        help="Only plot apps where every simpoint succeeded in every config",
    )
    p.add_argument(
        "--apps",
        nargs="+",
        default=None,
        help="Only include these app names (e.g. appworld core_bench terminal_bench)",
    )
    p.add_argument(
        "--allow-unmatched-baseline",
        action="store_true",
        help="If sweep/baseline simpoint IDs do not overlap, compare mean IPC vs mean baseline",
    )
    p.add_argument(
        "--per-threshold-complete",
        action="store_true",
        help="Only include a threshold bar when that app finished all simpoints at that threshold",
    )
    p.add_argument(
        "--suffix",
        default=None,
        help="Output filename suffix (default: _complete_apps / _agentic / empty)",
    )
    args = p.parse_args()

    if not args.baseline_dir.is_dir():
        raise SystemExit(
            f"Baseline dir not found: {args.baseline_dir}\n"
            "Restore with:\n"
            "  cd ~/scarab && git checkout 148a797f6 -- src/simulations/baseline"
        )

    configs = args.configs or detect_configs(args.root)
    if not configs:
        raise SystemExit(f"No threshold config dirs under {args.root}")

    out_dir = args.output_dir or (args.root / "hpca2027-plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    sweep, baseline, apps = collect_all(
        args.root,
        args.baseline_dir,
        configs,
        complete_only=args.complete_only,
        app_filter=args.apps,
        allow_unmatched_baseline=args.allow_unmatched_baseline,
    )
    if not apps:
        raise SystemExit(
            f"No apps to plot under {args.root}"
            + (" (complete-only filter)" if args.complete_only else "")
        )

    if args.per_threshold_complete:
        for app in apps:
            done = [
                thresh_label(c)
                for c in configs
                if threshold_fully_done(sweep.get(c, {}).get(app))
            ]
            print(f"  {app}: complete thresholds → {', '.join(done) if done else '(none)'}")
        sweep = mask_incomplete_thresholds(sweep, configs, apps)
        # Drop apps with no remaining complete threshold
        apps = [
            a
            for a in apps
            if any(threshold_fully_done(sweep.get(c, {}).get(a)) for c in configs)
        ]
        if not apps:
            raise SystemExit("No apps with any fully-complete threshold")

    missing_base = [a for a in apps if a not in baseline]
    print(f"Configs: {', '.join(configs)}")
    print(f"Sweep apps: {', '.join(apps)}")
    print(f"Baseline apps matched: {', '.join(a for a in apps if a in baseline)}")
    if missing_base:
        print(f"No baseline (skipped in speedup): {', '.join(missing_base)}")

    is_tt64 = any(c.startswith("tt64_") for c in configs)
    sweep_name = "TT=256 (64×4) threshold" if is_tt64 else "train-threshold"
    if args.suffix is not None:
        suffix = args.suffix
    elif args.complete_only:
        suffix = "_complete_apps"
    elif args.apps:
        suffix = "_agentic"
    else:
        suffix = ""

    note_bits = []
    if args.complete_only:
        note_bits.append("complete apps only")
    if args.apps:
        note_bits.append("selected apps")
    if args.per_threshold_complete:
        note_bits.append("only fully-finished thresholds")
    if args.allow_unmatched_baseline:
        note_bits.append("unmatched baseline OK")
    note = f" ({'; '.join(note_bits)})" if note_bits else ""

    write_summary_csv(
        sweep,
        baseline,
        apps,
        configs,
        out_dir / f"threshold_vs_baseline_summary{suffix}.csv",
    )
    plot_speedup_vs_baseline(
        sweep,
        apps,
        configs,
        out_dir / f"ipc_speedup_vs_baseline{suffix}.png",
        title=f"Runtime I-Fuse {sweep_name} sweep vs baseline{note}",
    )
    plot_absolute_ipc(
        sweep,
        baseline,
        apps,
        configs,
        out_dir / f"ipc_absolute{suffix}.png",
        title=f"Absolute IPC: baseline vs {sweep_name}{note}",
    )
    plot_coverage(
        sweep,
        apps,
        configs,
        out_dir / f"ifuse_load_coverage{suffix}.png",
        title=f"I-Fuse load coverage by insert threshold{note}",
    )
    print(f"Done. Outputs in {out_dir}")


if __name__ == "__main__":
    main()
