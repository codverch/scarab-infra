#!/usr/bin/env python3
"""Simpoint-weighted IPC speedup vs baseline for Helios, RFP, I-Fuse, and ideal fusion.

Expects scarab-infra simulation results under:
  {simulations-root}/baseline/<workload>/<cluster_id>/                         (flat)
  {simulations-root}/helios/<workload>/<cluster_id>/                           (flat)
  {simulations-root}/rfp/<workload>/<cluster_id>/                              (flat)
  {simulations-root}/ifuse/<workload>/<cluster_id>/
  {simulations-root}/ideal-fusion/<workload>/<cluster_id>/                     (flat)

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ipc.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/ipc

cd /users/deepmish/scarab
git add src/hpca2027-main-graphs-results/ipc/
git commit -m "Update HPCA main-graph IPC results."

Default paths live in this module (DEFAULT_RESULTS_ROOT, DEFAULT_*_OUTPUT_DIR).
Results are stored under scarab/src/hpca2027-main-graphs-results/<script>/.
When changing result locations, update those constants and the Commands block in
each hpca2027-main-graphs/plot_*.py file.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
SCRIPTS_DIR = SCARAB_INFRA_ROOT / "scripts"
NOTO_SERIF_FONT = GRAPH_DIR / "fonts" / "NotoSerif.ttf"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plot_pgo_ifuse_results import (  # noqa: E402
    SimpointKey,
    _ylim_speedup_pct_auto,
    _ylim_with_bar_label_headroom,
    load_stats_table,
)

try:
    from termcolor import colored
except ImportError:
    def colored(text: str, _color: str | None = None) -> str:
        return text


GAP_WORKLOADS = ["bfs", "dfs", "pagerank"]
AGENTIC_WORKLOADS = ["corebench", "appworld", "terminal_bench", "cachebench"]
DATABASE_WORKLOADS = [
    "clickhouse",
    "duckdb",
    "leveldb",
    "memcached",
]

WORKLOAD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GAP", tuple(GAP_WORKLOADS)),
    ("Agentic", tuple(AGENTIC_WORKLOADS)),
    ("Database", tuple(DATABASE_WORKLOADS)),
)
SIMPOINT_WORKLOADS = GAP_WORKLOADS + AGENTIC_WORKLOADS + DATABASE_WORKLOADS
DEFAULT_WORKLOADS_DB = SCARAB_INFRA_ROOT / "workloads" / "workloads_db.json"
TRACE_ROOT_SKIP_DIRS = frozenset({"datacenter", ".cache"})
EXCLUDED_SIMPOINT_TRACE_APPS = frozenset(
    {
        "sssp",
        "bc",
        "cc",
        "tc",
        "apsp",
        "cd",
        "tsp",
        "749-fotonik3d-r-ref",
        "782-lbm-r-ref",
        "800-pot3d-s-ref",
        "809-cactus-s-ref",
        "822-palm-s-ref",
    }
)

HELIOS_COLOR = "#CF3054"
RFP_COLOR = "#663399"
IFUSE_COLOR = "#69F336"
BASELINE_COLOR = "#808080"
IDEAL_FUSION_COLOR = "#006D6F"
AVERAGE_SEPARATOR_COLOR = "#4A4A4A"
AVERAGE_SEPARATOR_WIDTH = 5.0
SMALL_BAR_THRESHOLD = 0.5
BAR_LABEL_GAP = 1.2
BAR_WIDTH = 0.44
BAR_EDGE_WIDTH = 2.5
FONT_FAMILY = "Noto Serif"  # NotoSerif (fonts-noto-core)
GROUP_GAP = 0.0
APP_STEP = 1.9
AVERAGE_GAP = 1.15
AVERAGE_SEPARATOR_FRAC = 0.5
X_TICK_GUIDE_WIDTH = 2.5
X_TICK_GUIDE_LENGTH = 0.04
LEGEND_X_OFFSET = 0.01
IPC_TICK_FONT = 37
IPC_AXIS_LABEL_FONT = IPC_TICK_FONT
IPC_LEGEND_FONT = IPC_TICK_FONT
IPC_AXIS_FONT = 30


def register_noto_serif() -> None:
    """Register the bundled Noto Serif font used by HPCA plots."""
    global FONT_FAMILY
    if not NOTO_SERIF_FONT.is_file():
        return
    from matplotlib import font_manager

    font_manager.fontManager.addfont(str(NOTO_SERIF_FONT))
    FONT_FAMILY = font_manager.FontProperties(fname=str(NOTO_SERIF_FONT)).get_name()

# Backward-compatible aliases used by other hpca2027-main-graphs scripts.
MAROON_COLOR = AVERAGE_SEPARATOR_COLOR
ARROW_THRESHOLD = SMALL_BAR_THRESHOLD

IPC_SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ideal_fusion", "Ideal fusion", IDEAL_FUSION_COLOR),
)

DEFAULT_TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
DEFAULT_SUITE = "datacenter"
DEFAULT_SUBSUITE = "datacenter"
DEFAULT_SCARAB_ROOT = Path("/users/deepmish/scarab")
DEFAULT_SIMULATIONS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "simulations"
DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-main-graphs-results"
DEFAULT_RESULTS_GIT_PATH = "src/hpca2027-main-graphs-results"

DEFAULT_IPC_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "ipc"
DEFAULT_IFUSE_ACCURACY_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "ifuse_accuracy"
DEFAULT_IFUSE_COVERAGE_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "ifuse_coverage"
DEFAULT_FUSION_FRACTION_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "fusion_fraction"
DEFAULT_LOAD_LATENCY_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "load_latency"
DEFAULT_READ_PORT_STALLS_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "read_port_stalls"
DEFAULT_ROB_STALLS_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "rob_stalls"
DEFAULT_DCACHE_ACCESSES_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "dcache_accesses"
DEFAULT_REGISTER_FILE_UTILIZATION_OUTPUT_DIR = (
    DEFAULT_RESULTS_ROOT / "register_file_utilization"
)
DEFAULT_TRAIN_THRESHOLD_SWEEP_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "train_threshold_sweep"
DEFAULT_COMPLETED_RUNTIME_IFUSE_IPC_OUTPUT_DIR = (
    DEFAULT_RESULTS_ROOT / "completed_runtime_ifuse_ipc"
)

DEFAULT_BASELINE_DIR = DEFAULT_SIMULATIONS_ROOT / "baseline"
DEFAULT_BASELINE_IFUSE_DIR = DEFAULT_SIMULATIONS_ROOT / "baseline-ifuse"
DEFAULT_HELIOS_DIR = DEFAULT_SIMULATIONS_ROOT / "helios"
DEFAULT_RFP_DIR = DEFAULT_SIMULATIONS_ROOT / "rfp"
DEFAULT_RFP_BASELINE_DIR = DEFAULT_SIMULATIONS_ROOT / "rfp-baseline"
DEFAULT_IFUSE_DIR = DEFAULT_SIMULATIONS_ROOT / "ifuse-tt512-threshold-100"
DEFAULT_IPC_IFUSE_DIR = DEFAULT_IFUSE_DIR
DEFAULT_IDEAL_DIR = DEFAULT_SIMULATIONS_ROOT / "ideal-fusion"

DEFAULT_BASELINE_CONFIG = "baseline"
DEFAULT_HELIOS_CONFIG = "helios"
DEFAULT_RFP_CONFIG = "rfp"
DEFAULT_IFUSE_CONFIG = "ifuse"
DEFAULT_IPC_IFUSE_CONFIG = "datacenter"
DEFAULT_IDEAL_CONFIG = "ideal-fusion"


def rename_workload(workload: str) -> str:
    mapping = {
        "bfs": "BFS",
        "dfs": "DFS",
        "pagerank": "PR",
        "sssp": "SSSP",
        "sssp_ego_fb": "SSSP",
        "bc": "BC",
        "cc": "CC",
        "tc": "TC",
        "apsp": "APSP",
        "cd": "CD",
        "tsp": "TSP",
        "appworld": "AppWorld",
        "core_bench": "CoreBench",
        "corebench": "CoreBench",
        "terminal_bench": "TerminalBench",
        "cachebench": "CacheBench",
        "leveldb": "LevelDB",
        "clickhouse": "ClickHouse",
        "rocksdb": "RocksDB",
        "duckdb": "DuckDB",
        "memcached": "Memcached",
        "masstree": "Masstree",
        "749-fotonik3d-r-ref": "fotonik3d",
        "782-lbm-r-ref": "lbm",
        "800-pot3d-s-ref": "pot3d",
        "809-cactus-s-ref": "cactus",
        "822-palm-s-ref": "palm",
    }
    return mapping.get(workload, workload)


def discover_simpoint_trace_workloads(trace_root: Path) -> list[str]:
    """Return apps under trace_root that have opt.p/opt.w simpoint weights."""
    if not trace_root.is_dir():
        raise SystemExit(f"Trace root not found: {trace_root}")
    apps: list[str] = []
    for path in sorted(trace_root.iterdir()):
        if not path.is_dir() or path.name in TRACE_ROOT_SKIP_DIRS:
            continue
        simpoints = path / "simpoints"
        if (simpoints / "opt.p").is_file() and (simpoints / "opt.w").is_file():
            apps.append(path.name)
    if not apps:
        raise SystemExit(f"No simpoint-trace apps found under {trace_root}")
    return apps


def order_workloads_by_group(workloads: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for _group, members in WORKLOAD_GROUPS:
        for wl in members:
            if wl in workloads and wl not in seen:
                ordered.append(wl)
                seen.add(wl)
    for wl in workloads:
        if wl not in seen:
            ordered.append(wl)
    return ordered


def grouped_x_positions(
    workloads: list[str],
    *,
    n_series: int = len(IPC_SERIES),
) -> tuple[list[str], dict[str, float], float, float]:
    """Return grouped workload order, x map, average column x, and separator x."""
    ordered = order_workloads_by_group(workloads)
    x_map: dict[str, float] = {}
    x = 0.0
    for wl in ordered:
        x_map[wl] = x
        x += APP_STEP
    last_x = x_map[ordered[-1]]
    cluster_half = (n_series * BAR_WIDTH) / 2.0
    avg_x = last_x + cluster_half + AVERAGE_GAP + cluster_half
    separator_x = last_x + cluster_half + AVERAGE_GAP * AVERAGE_SEPARATOR_FRAC
    return ordered, x_map, avg_x, separator_x


def optional_stats_csv(experiment_dir: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    primary = experiment_dir / "collected_stats.csv"
    return primary if primary.is_file() else None


def load_workloads_db_weights(
    workloads_db: Path,
    workloads: list[str],
) -> dict[tuple[str, str], float]:
    """Load cluster_id weights from workloads_db.json (datacenter/datacenter)."""
    if not workloads_db.is_file():
        return {}
    with workloads_db.open() as fh:
        db = json.load(fh)
    suite = db.get("datacenter", {}).get("datacenter", {})
    weights: dict[tuple[str, str], float] = {}
    for workload in workloads:
        entry = suite.get(workload)
        if not entry:
            continue
        for sp in entry.get("simpoints", []):
            cid = str(sp["cluster_id"])
            weight = float(sp["weight"])
            if weight > 0:
                weights[(workload, cid)] = weight
    return weights


def load_simpoint_trace_weights(
    trace_root: Path,
    workloads: list[str],
    *,
    workloads_db: Path | None = None,
) -> dict[tuple[str, str], float]:
    """Load simpoint weights, preferring workloads_db cluster_id weights when present.

    workloads_db matches simulation directory names (cluster_id). Trace opt.p/opt.w
    keys can disagree with result dirs for some apps (e.g. memcached).
    """
    weights: dict[tuple[str, str], float] = {}
    db_weights = (
        load_workloads_db_weights(workloads_db, workloads)
        if workloads_db is not None
        else {}
    )

    for workload in workloads:
        db_keys = {cid for (wl, cid) in db_weights if wl == workload}
        if db_keys:
            for cid in db_keys:
                weights[(workload, cid)] = db_weights[(workload, cid)]
            continue

        simpoints_dir = trace_root / workload / "simpoints"
        pfile = simpoints_dir / "opt.p"
        wfile = simpoints_dir / "opt.w"
        if not pfile.is_file() or not wfile.is_file():
            raise SystemExit(f"Missing simpoint weights for {workload}: {simpoints_dir}")

        trace_to_sim: dict[str, str] = {}
        with pfile.open() as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2:
                    trace_to_sim[parts[0]] = parts[1]

        sim_to_weight: dict[str, float] = {}
        with wfile.open() as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2:
                    sim_to_weight[parts[1]] = float(parts[0])

        for trace_id, sim_id in trace_to_sim.items():
            weight = sim_to_weight.get(sim_id)
            if weight is None or weight <= 0:
                continue
            weights[(workload, trace_id)] = weight
    return weights


def ipc_from_sim_dir(sim_dir: Path) -> float | None:
    core_stat = sim_dir / "core.stat.0.csv"
    if not core_stat.is_file():
        return None

    periodic_instructions: float | None = None
    periodic_cycles: float | None = None
    with core_stat.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            stat = row[0].strip()
            if stat == "Periodic_Instructions":
                periodic_instructions = float(row[2].strip())
            elif stat == "Periodic_Cycles":
                periodic_cycles = float(row[2].strip())

    if (
        periodic_instructions is None
        or periodic_cycles is None
        or periodic_cycles <= 0
    ):
        return None
    return periodic_instructions / periodic_cycles


def find_simpoint_dir(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    suite: str,
    subsuite: str,
) -> Path | None:
    """Resolve simpoint dir for nested or flat experiment layouts."""
    candidates = [
        experiment_dir / config / suite / subsuite / workload / cluster_id,
        experiment_dir / suite / subsuite / workload / cluster_id,
        experiment_dir / config / workload / cluster_id,
        experiment_dir / workload / cluster_id,
    ]
    for path in candidates:
        if path.is_dir() and (path / "core.stat.0.csv").is_file():
            return path
    return None


def load_ipc_from_stats_csv(stats_csv: Path, config: str) -> dict[SimpointKey, float]:
    table, _ = load_stats_table(stats_csv)
    ipc_row = table.get("IPC", {})
    return {key: val for key, val in ipc_row.items() if key.config == config}


def supplement_ipc_from_sim_dirs(
    ipc: dict[SimpointKey, float],
    experiment_dir: Path,
    config: str,
    sp_weights: dict[tuple[str, str], float],
    workloads: list[str],
    *,
    suite: str,
    subsuite: str,
) -> int:
    added = 0
    for workload in workloads:
        for (wl, cid), _weight in sp_weights.items():
            if wl != workload:
                continue
            sim_dir = find_simpoint_dir(
                experiment_dir, config, wl, cid, suite=suite, subsuite=subsuite
            )
            if sim_dir is None:
                continue
            value = ipc_from_sim_dir(sim_dir)
            if value is None:
                continue
            key = SimpointKey(config, wl, cid)
            if key not in ipc:
                added += 1
            ipc[key] = value
    return added


def load_experiment_ipc(
    experiment_dir: Path,
    config: str,
    sp_weights: dict[tuple[str, str], float],
    workloads: list[str],
    *,
    stats_csv: Path | None,
    suite: str,
    subsuite: str,
    label: str,
) -> dict[SimpointKey, float]:
    ipc: dict[SimpointKey, float] = {}
    if stats_csv is not None:
        ipc.update(load_ipc_from_stats_csv(stats_csv, config))
        print(f"  {label}: loaded {len(ipc)} IPC values from {stats_csv}")
    added = supplement_ipc_from_sim_dirs(
        ipc, experiment_dir, config, sp_weights, workloads,
        suite=suite, subsuite=subsuite,
    )
    if added:
        print(f"  {label}: supplemented {added} IPC values from sim dirs under {experiment_dir}")
    return ipc


def reference_traces_for_workload(
    experiment_dir: Path,
    config: str,
    workload: str,
    sp_weights: dict[tuple[str, str], float],
    *,
    suite: str,
    subsuite: str,
) -> set[str]:
    traces: set[str] = set()
    for wl, cid in sp_weights:
        if wl != workload:
            continue
        if find_simpoint_dir(
            experiment_dir, config, wl, cid, suite=suite, subsuite=subsuite
        ):
            traces.add(cid)
    return traces


def check_simpoint_coverage(
    directories: dict[str, tuple[Path, str]],
    workloads: list[str],
    sp_weights: dict[tuple[str, str], float],
    *,
    suite: str,
    subsuite: str,
    report_path: Path,
    optional_configs: set[str] | None = None,
) -> tuple[set[str], dict[str, set[str]]]:
    """Return (complete workloads, reference trace ids per workload).

    Reference traces are the intersection of weighted simpoints present in
    baseline and every required (non-optional) experiment. Apps with a
    non-empty intersection are included so partial result sets still plot.
    """
    optional = optional_configs or set()
    complete_apps: set[str] = set()
    reference_by_workload: dict[str, set[str]] = {}
    all_missing: dict[str, dict[str, list[str]]] = defaultdict(dict)

    required_labels = [
        label for label in directories if label == "baseline" or label not in optional
    ]

    with report_path.open("w") as rpt:
        rpt.write("=" * 100 + "\n")
        rpt.write("SIMPOINT COVERAGE REPORT\n")
        rpt.write(
            "Reference: intersection of weighted traces present in baseline and "
            "all required experiments.\n"
        )
        rpt.write("=" * 100 + "\n\n")

        for workload in workloads:
            rpt.write(f"\n{'=' * 80}\n")
            rpt.write(f"APP: {workload}\n")
            rpt.write(f"{'=' * 80}\n")

            present_by_label: dict[str, set[str]] = {}
            for label, (exp_dir, config) in directories.items():
                found: set[str] = set()
                for (wl, cid), _weight in sp_weights.items():
                    if wl != workload:
                        continue
                    sim_dir = find_simpoint_dir(
                        exp_dir, config, wl, cid, suite=suite, subsuite=subsuite
                    )
                    if sim_dir and ipc_from_sim_dir(sim_dir) is not None:
                        found.add(cid)
                present_by_label[label] = found

            baseline_traces = present_by_label.get("baseline", set())
            if not baseline_traces:
                rpt.write(f"  !! No baseline simpoints for {workload} — skipping\n")
                all_missing[workload]["baseline"] = ["NO BASELINE SIMPOINTS"]
                continue

            required_sets = [present_by_label[label] for label in required_labels]
            reference_traces = set.intersection(*required_sets) if required_sets else set()
            reference_by_workload[workload] = reference_traces

            rpt.write(f"  Baseline traces: {sorted(baseline_traces)}\n")
            rpt.write(f"  Intersection reference: {sorted(reference_traces)}\n")
            rpt.write(f"  Total reference traces: {len(reference_traces)}\n")

            app_complete = bool(reference_traces)
            for label, (exp_dir, _config) in directories.items():
                found = present_by_label[label]
                rpt.write(f"\n  [{label}]  ({exp_dir})\n")
                missing_vs_baseline = baseline_traces - found
                if missing_vs_baseline:
                    if label in optional:
                        rpt.write(
                            f"    OPTIONAL MISSING vs baseline ({len(missing_vs_baseline)}): "
                            f"{sorted(missing_vs_baseline)}\n"
                        )
                    elif label != "baseline":
                        rpt.write(
                            f"    MISSING vs baseline ({len(missing_vs_baseline)}): "
                            f"{sorted(missing_vs_baseline)}\n"
                        )
                        if not reference_traces:
                            all_missing[workload][label] = sorted(missing_vs_baseline)
                    else:
                        rpt.write(
                            f"    OK  - {len(found)} baseline trace(s)\n"
                        )
                else:
                    rpt.write(f"    OK  - All {len(baseline_traces)} baseline trace(s) present.\n")
                rpt.write(f"    Found: {sorted(found)}\n")

            if app_complete:
                complete_apps.add(workload)
                dropped = baseline_traces - reference_traces
                if dropped:
                    rpt.write(
                        f"\n  >> RESULT: COMPLETE via intersection "
                        f"(dropped vs baseline: {sorted(dropped)})\n"
                    )
                else:
                    rpt.write("\n  >> RESULT: COMPLETE - will be included in plot\n")
            else:
                rpt.write("\n  >> RESULT: INCOMPLETE - will be EXCLUDED from plot\n")

        rpt.write(f"\n\n{'=' * 100}\n")
        rpt.write(f"COMPLETE apps ({len(complete_apps)}): {sorted(complete_apps)}\n")
        incomplete = set(workloads) - complete_apps
        rpt.write(f"INCOMPLETE apps ({len(incomplete)}): {sorted(incomplete)}\n")
        rpt.write(
            "OVERALL: "
            + ("ALL PRESENT" if not incomplete else "SOME FILES MISSING")
            + "\n"
        )
        rpt.write("=" * 100 + "\n")

    print(f"\nSimpoint coverage report written to: {report_path}")

    if all_missing:
        print(colored("\n" + "=" * 80, "red"))
        print(colored("MISSING SIMPOINT FILES — regenerate the following:", "red"))
        print(colored("=" * 80, "red"))
        for workload in sorted(all_missing):
            print(colored(f"\n  APP: {workload}", "yellow"))
            for label, missing_traces in sorted(all_missing[workload].items()):
                exp_dir, _config = directories[label]
                print(colored(f"    [{label}]  →  {exp_dir}", "cyan"))
                for trace in missing_traces:
                    trace_zip = DEFAULT_TRACE_ROOT / workload / "traces" / f"{trace}.zip"
                    print(
                        colored(f"      missing trace ID: {trace}", "red")
                        + f"  (source trace: {trace_zip})"
                    )
        print(colored("=" * 80 + "\n", "red"))
    else:
        print(colored("\nAll simpoint files present in all directories.\n", "green"))

    excluded = set(workloads) - complete_apps
    if excluded:
        print(
            colored(
                f"WARNING: {len(excluded)} app(s) excluded from plot: {sorted(excluded)}",
                "yellow",
            )
        )

    return complete_apps, reference_by_workload


def collect_trace_pairs(
    ipc: dict[SimpointKey, float],
    sp_weights: dict[tuple[str, str], float],
    *,
    config: str,
    workload: str,
    reference_traces: set[str],
) -> list[tuple[str, float, float]]:
    """Return [(cluster_id, ipc, weight), ...] for baseline reference traces only."""
    pairs: list[tuple[str, float, float]] = []
    for cid in sorted(reference_traces, key=lambda x: int(x) if x.isdigit() else x):
        weight = sp_weights.get((workload, cid))
        if weight is None:
            continue
        key = SimpointKey(config, workload, cid)
        value = ipc.get(key)
        if value is None or math.isnan(value):
            continue
        pairs.append((cid, value, weight))
    return pairs


def weighted_avg(pairs: list[tuple[str, float, float]]) -> float:
    total_w = sum(weight for _, _, weight in pairs)
    if total_w == 0:
        return float("nan")
    return sum(ipc * weight for _, ipc, weight in pairs) / total_w


def write_computation_log(
    path: Path,
    workloads: list[str],
    baseline_avg: list[float],
    series_pairs: dict[str, dict[str, list[tuple[str, float, float]]]],
    series_avg: dict[str, list[float]],
    series_normalized: dict[str, list[float]],
    *,
    series: tuple[tuple[str, str, str], ...] = IPC_SERIES,
) -> None:
    with path.open("w") as log:
        log.write("=" * 100 + "\n")
        log.write("DETAILED IPC COMPUTATION LOG\n")
        log.write("=" * 100 + "\n\n")

        for idx, workload in enumerate(workloads):
            log.write(f"\n{'=' * 80}\n")
            log.write(f"APP: {workload}\n")
            log.write(f"{'=' * 80}\n")

            log.write("\n--- BASELINE ---\n")
            log.write(f"Weighted Average IPC: {baseline_avg[idx]:.6f}\n")

            for key, label, _color in series:
                pairs = series_pairs[key][workload]
                avg = series_avg[key][idx]
                log.write(f"\n--- {label.upper()} ---\n")
                log.write(f"Number of traces: {len(pairs)}\n")
                total_weight = sum(w for _, _, w in pairs)
                log.write(f"Total weight: {total_weight:.6f}\n")
                log.write("Traces (trace_id, IPC, Weight, IPC*Weight):\n")
                for trace_id, ipc_val, weight in pairs:
                    log.write(
                        f"  Trace {trace_id}: IPC={ipc_val:.6f}, "
                        f"Weight={weight:.6f}, Contribution={ipc_val * weight:.6f}\n"
                    )
                log.write(f"Weighted Average IPC: {avg:.6f}\n")

            baseline_value = baseline_avg[idx]
            log.write(f"\n--- SUMMARY FOR {workload} ---\n")
            log.write(f"Baseline: {baseline_value:.6f}\n")
            for key, label, _color in series:
                avg = series_avg[key][idx]
                norm = series_normalized[key][idx]
                log.write(f"{label:14s} {avg:.6f} (normalized: {norm:.4f})\n")

        log.write(f"\n\n{'=' * 100}\n")
        log.write("NORMALIZED VALUES (to Baseline)\n")
        log.write("=" * 100 + "\n")
        for idx, workload in enumerate(workloads):
            log.write(f"\n{workload}:\n")
            log.write("  Baseline normalized: 1.0000\n")
            for key, label, _color in series:
                log.write(
                    f"  {label} normalized:    {series_normalized[key][idx]:.4f}\n"
                )
        log.write("\n" + "=" * 100 + "\n")
        log.write("LOG FILE COMPLETE\n")
        log.write("=" * 100 + "\n")


def write_summary_csv(path: Path, rows: list[dict[str, str | float]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]


def _apply_ipc_plot_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": [FONT_FAMILY, "NotoSerif", "DejaVu Serif", "serif"],
            "font.size": IPC_AXIS_FONT,
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
            "legend.fontsize": IPC_LEGEND_FONT,
        }
    )


def _annotate_ipc_bar_labels(
    ax,
    container,
    values: list[float],
    *,
    fontsize: int = IPC_AXIS_FONT,
    small_values_only: bool = False,
    label_lane: int = 0,
    n_label_lanes: int = 1,
) -> None:
    """Label bar speedups; tiny values use a downward arrow matching the bar color."""
    import matplotlib.colors as mcolors

    lane_step = 3.0
    lane_base = 3.0
    small_fontsize = fontsize
    for patch, val in zip(container.patches, values, strict=True):
        if math.isnan(val) or val < 0:
            continue
        x = patch.get_x() + patch.get_width() / 2.0
        bar_color = mcolors.to_hex(patch.get_facecolor())
        if val < SMALL_BAR_THRESHOLD:
            bar_top = patch.get_height()
            arrow_target = max(bar_top + 0.08, 0.12)
            arrow_top = max(
                bar_top + 2.5,
                lane_base + label_lane * lane_step,
            )
            ax.annotate(
                "",
                xy=(x, arrow_target),
                xytext=(x, arrow_top),
                arrowprops=dict(
                    arrowstyle="->",
                    color=bar_color,
                    lw=1.5,
                    mutation_scale=12,
                    shrinkA=0,
                    shrinkB=2,
                ),
                zorder=10,
            )
            ax.text(
                x - 0.03,
                arrow_top,
                f"{val:+.1f}",
                ha="right",
                va="bottom",
                fontsize=small_fontsize,
                fontfamily=FONT_FAMILY,
                color=bar_color,
                bbox=dict(facecolor="white", edgecolor="none", pad=0.15),
                zorder=11,
            )
            continue

        if small_values_only:
            continue

        height = patch.get_height()
        ax.text(
            x,
            height + BAR_LABEL_GAP,
            f"{val:+.1f}",
            ha="center",
            va="bottom",
            fontsize=fontsize,
            fontfamily=FONT_FAMILY,
            color="black",
            rotation=90,
            zorder=4,
        )


def _ipc_legend_handles(
    series: tuple[tuple[str, str, str], ...],
) -> list:
    from matplotlib.patches import Patch

    return [
        Patch(
            facecolor=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            label=label,
        )
        for _key, label, color in series
    ]


def _tight_x_limits(ax, x_min: float, x_max: float, n_bars: int) -> None:
    """Trim left/right plot margins while leaving room for outer bar edges."""
    left_pad = 0.55
    right_pad = 0.55
    half_span = (n_bars * BAR_WIDTH) / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


def _ylim_snap_to_tens(ylim: tuple[float, float]) -> tuple[float, float]:
    _ymin, ymax = ylim
    ymin_snapped = 0.0
    ymax_snapped = math.ceil(max(0.0, ymax) / 5.0) * 5.0
    if ymax_snapped <= ymin_snapped:
        ymax_snapped = ymin_snapped + 5.0
    return (ymin_snapped, ymax_snapped)


def _apply_speedup_y_ticks(ax) -> None:
    import matplotlib.ticker as mticker

    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.yaxis.set_minor_locator(mticker.MultipleLocator(5))
    ax.tick_params(axis="y", which="minor", length=0)


def _apply_speedup_y_grid(ax) -> None:
    grid_style = {
        "axis": "y",
        "alpha": 0.8,
        "linestyle": ":",
        "color": "black",
        "linewidth": 2.0,
        "zorder": 0,
    }
    ax.grid(True, which="major", **grid_style)
    ax.grid(True, which="minor", **grid_style)


def _draw_app_x_tick_guides(ax, x_ticks: list[float]) -> None:
    from matplotlib.transforms import blended_transform_factory

    trans = blended_transform_factory(ax.transData, ax.transAxes)
    for x in x_ticks:
        ax.plot(
            [x, x],
            [0.0, -X_TICK_GUIDE_LENGTH],
            transform=trans,
            color="black",
            linewidth=X_TICK_GUIDE_WIDTH,
            solid_capstyle="butt",
            clip_on=False,
            zorder=6,
        )


def plot_speedup_bars(
    workloads: list[str],
    series_normalized: dict[str, list[float]],
    output_dir: Path,
    *,
    series: tuple[tuple[str, str, str], ...] = IPC_SERIES,
    file_prefix: str = "ipc",
) -> None:
    import matplotlib.pyplot as plt

    series_pct: dict[str, list[float]] = {
        key: [
            (val - 1.0) * 100.0 if not math.isnan(val) else float("nan")
            for val in series_normalized[key]
        ]
        for key, _, _ in series
    }
    offsets = _bar_offsets(len(series))
    ordered, x_map, avg_x, separator_x = grouped_x_positions(
        workloads, n_series=len(series)
    )
    display_apps = [rename_workload(wl) for wl in ordered] + ["Average"]
    x_ticks = [x_map[wl] for wl in ordered] + [avg_x]

    for key, _, _ in series:
        values = [val for val in series_normalized[key] if not math.isnan(val)]
        arithmetic_mean = sum(values) / len(values) if values else float("nan")
        if math.isnan(arithmetic_mean):
            series_pct[key].append(float("nan"))
        else:
            series_pct[key].append((arithmetic_mean - 1.0) * 100.0)

    pct_sets = [
        [max(0.0, v) if not math.isnan(v) else v for v in series_pct[key]]
        for key, _, _ in series
    ]
    base_ylim = _ylim_speedup_pct_auto(*pct_sets)

    variants = (
        (f"{file_prefix}-labeled", True, _ylim_with_bar_label_headroom(base_ylim)),
        (file_prefix, False, base_ylim),
    )

    for stem, show_bar_labels, ylim in variants:
        _apply_ipc_plot_style()
        fig_width = max(22.0, len(x_ticks) * APP_STEP * 1.15 + AVERAGE_GAP)
        fig, ax = plt.subplots(figsize=(fig_width, 7.0))

        for series_idx, ((_key, _label, color), offset) in enumerate(zip(series, offsets)):
            wl_to_pct = dict(zip(workloads, series_pct[_key][:-1]))
            avg_pct = series_pct[_key][-1]
            pct_vals = [wl_to_pct[wl] for wl in ordered] + [avg_pct]
            bar_x = [x_map[wl] + offset for wl in ordered] + [avg_x + offset]
            container = ax.bar(
                bar_x,
                [0.0 if math.isnan(val) else max(0.0, val) for val in pct_vals],
                BAR_WIDTH,
                color=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                zorder=3,
            )
            if show_bar_labels:
                _annotate_ipc_bar_labels(
                    ax,
                    container,
                    pct_vals,
                    fontsize=IPC_AXIS_FONT,
                    label_lane=series_idx,
                    n_label_lanes=len(series),
                )

        if len(x_ticks) > 1:
            ax.axvline(
                x=separator_x,
                color=AVERAGE_SEPARATOR_COLOR,
                linestyle="--",
                alpha=1.0,
                linewidth=AVERAGE_SEPARATOR_WIDTH,
                zorder=2,
            )

        ax.set_xticks(x_ticks)
        ax.set_xticklabels(
            display_apps,
            rotation=45,
            ha="right",
            fontsize=IPC_TICK_FONT,
            fontfamily=FONT_FAMILY,
        )
        for i, label in enumerate(ax.get_xticklabels()):
            if i == len(display_apps) - 1:
                label.set_weight("bold")

        _tight_x_limits(ax, x_ticks[0], x_ticks[-1], n_bars=len(series))

        ax.set_ylabel(
            "Speedup (%)\n(normalized to\nno-fusion)",
            fontsize=IPC_AXIS_LABEL_FONT,
            fontfamily=FONT_FAMILY,
        )
        ax.yaxis.set_label_coords(-0.045, 0.5)
        ax.set_ylim(ylim[0], ylim[1])
        snapped_ylim = _ylim_snap_to_tens(ax.get_ylim())
        ax.set_ylim(snapped_ylim[0], snapped_ylim[1] + 5.0)
        _apply_speedup_y_ticks(ax)
        _apply_speedup_y_grid(ax)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
        ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
        ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
        for label in ax.get_yticklabels():
            label.set_fontfamily(FONT_FAMILY)
        _draw_app_x_tick_guides(ax, x_ticks)

        x_min, x_max = ax.get_xlim()
        separator_x_frac = (separator_x - x_min) / (x_max - x_min) - LEGEND_X_OFFSET
        legend = ax.legend(
            handles=_ipc_legend_handles(series),
            frameon=True,
            fancybox=False,
            shadow=False,
            loc="upper right",
            bbox_to_anchor=(separator_x_frac, 0.98),
            bbox_transform=ax.transAxes,
            fontsize=IPC_LEGEND_FONT,
            edgecolor="black",
            ncol=len(series),
            handlelength=0.95,
            handleheight=0.95,
            borderpad=0.55,
            labelspacing=0.4,
            columnspacing=1.0,
            framealpha=1.0,
        )
        legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
        legend.get_frame().set_facecolor("white")
        legend.get_frame().set_alpha(1.0)

        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("black")
            spine.set_linewidth(2.5)

        plt.tight_layout()
        plt.subplots_adjust(top=0.88, bottom=0.28, right=0.98)

        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.12, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.12, dpi=300)
        plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted IPC speedup vs baseline for "
            "Helios, RFP, I-Fuse, and ideal fusion."
        )
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-stats-csv", type=Path, default=None)
    parser.add_argument("--helios-stats-csv", type=Path, default=None)
    parser.add_argument("--rfp-stats-csv", type=Path, default=None)
    parser.add_argument("--ifuse-stats-csv", type=Path, default=None)
    parser.add_argument("--ideal-fusion-stats-csv", type=Path, default=None)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument(
        "--ifuse-config",
        default=DEFAULT_IPC_IFUSE_CONFIG,
        help="Config name for I-Fuse simpoint paths (default: datacenter).",
    )
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/ipc)",
    )
    parser.add_argument(
        "--file-prefix",
        default="ipc",
        help="Output filename prefix (default: ipc).",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=[])
    parser.add_argument(
        "--all-simpoint-traces",
        action="store_true",
        help=(
            "Plot every app under --trace-root that has simpoint weights "
            "(instead of the default curated SIMPOINT_WORKLOADS list)."
        ),
    )
    parser.add_argument(
        "--workloads-db",
        type=Path,
        default=DEFAULT_WORKLOADS_DB,
        help="workloads_db.json used for cluster_id weights (default: %(default)s)",
    )
    parser.add_argument(
        "--schemes",
        nargs="*",
        default=None,
        metavar="SCHEME",
        help=(
            "IPC series to plot (subset of: helios, rfp, ifuse, ideal_fusion). "
            "Default: all series."
        ),
    )
    parser.add_argument(
        "--optional-configs",
        nargs="*",
        default=["helios"],
        help="Configs that may have missing simpoints without excluding the app.",
    )
    args = parser.parse_args()

    scheme_keys = tuple(args.schemes) if args.schemes else tuple(key for key, _, _ in IPC_SERIES)
    unknown = [key for key in scheme_keys if key not in {k for k, _, _ in IPC_SERIES}]
    if unknown:
        raise SystemExit(f"Unknown scheme(s): {', '.join(unknown)}")
    active_series = tuple(entry for entry in IPC_SERIES if entry[0] in scheme_keys)
    if not active_series:
        raise SystemExit("No schemes selected for plotting.")

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or DEFAULT_BASELINE_DIR
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR

    if args.all_simpoint_traces:
        candidate_workloads = discover_simpoint_trace_workloads(args.trace_root)
        candidate_workloads = [
            wl for wl in candidate_workloads if wl not in EXCLUDED_SIMPOINT_TRACE_APPS
        ]
    else:
        candidate_workloads = list(SIMPOINT_WORKLOADS)
    workloads = [wl for wl in candidate_workloads if wl not in set(args.exclude_workloads)]
    if not workloads:
        raise SystemExit("No workloads left after exclusions.")

    output_dir = args.output_dir or DEFAULT_IPC_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(
        args.trace_root,
        workloads,
        workloads_db=args.workloads_db,
    )

    baseline_csv = optional_stats_csv(baseline_dir, args.baseline_stats_csv)
    helios_csv = optional_stats_csv(helios_dir, args.helios_stats_csv)
    rfp_csv = optional_stats_csv(rfp_dir, args.rfp_stats_csv)
    ifuse_csv = optional_stats_csv(ifuse_dir, args.ifuse_stats_csv)
    ideal_csv = optional_stats_csv(ideal_dir, args.ideal_fusion_stats_csv)

    print("Loading IPC (collected_stats.csv + sim directories)...")
    print(f"  baseline:     {baseline_dir} (config={args.baseline_config})")
    print(f"  helios:       {helios_dir} (config={args.helios_config})")
    print(f"  rfp:          {rfp_dir} (config={args.rfp_config})")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    baseline_ipc = load_experiment_ipc(
        baseline_dir, args.baseline_config, sp_weights, workloads,
        stats_csv=baseline_csv, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE,
        label="baseline",
    )
    helios_ipc = load_experiment_ipc(
        helios_dir, args.helios_config, sp_weights, workloads,
        stats_csv=helios_csv, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE,
        label="helios",
    )
    rfp_ipc = load_experiment_ipc(
        rfp_dir, args.rfp_config, sp_weights, workloads,
        stats_csv=rfp_csv, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE,
        label="rfp",
    )
    ifuse_ipc = load_experiment_ipc(
        ifuse_dir, args.ifuse_config, sp_weights, workloads,
        stats_csv=ifuse_csv, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE,
        label="ifuse",
    )
    ideal_ipc = load_experiment_ipc(
        ideal_dir, args.ideal_fusion_config, sp_weights, workloads,
        stats_csv=ideal_csv, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE,
        label="ideal fusion",
    )

    ipc_by_label = {
        "baseline": baseline_ipc,
        "helios": helios_ipc,
        "rfp": rfp_ipc,
        "ifuse": ifuse_ipc,
        "ideal_fusion": ideal_ipc,
    }
    all_directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "helios": (helios_dir, args.helios_config),
        "rfp": (rfp_dir, args.rfp_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config),
    }
    directories = {
        key: all_directories[key]
        for key in ("baseline", *scheme_keys)
    }
    config_by_label = {label: cfg for label, (_d, cfg) in all_directories.items()}

    complete_apps, reference_by_workload = check_simpoint_coverage(
        directories,
        workloads,
        sp_weights,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
        report_path=output_dir / "simpoint_coverage_report.txt",
        optional_configs=set(args.optional_configs),
    )
    if not complete_apps:
        scheme_list = ", ".join(scheme_keys)
        raise SystemExit(
            f"No apps have complete simpoint files across baseline and: {scheme_list}."
        )

    optional_configs = set(args.optional_configs)
    apps = [wl for wl in workloads if wl in complete_apps]

    baseline_pairs: dict[str, list[tuple[str, float, float]]] = {}
    series_pairs: dict[str, dict[str, list[tuple[str, float, float]]]] = {
        key: {} for key, _, _ in active_series
    }
    baseline_avg: list[float] = []
    series_avg: dict[str, list[float]] = {key: [] for key, _, _ in active_series}
    series_normalized: dict[str, list[float]] = {key: [] for key, _, _ in active_series}

    for workload in apps:
        ref = reference_by_workload[workload]
        bp = collect_trace_pairs(
            baseline_ipc, sp_weights, config=args.baseline_config,
            workload=workload, reference_traces=ref,
        )
        baseline_pairs[workload] = bp
        b_avg = weighted_avg(bp)
        baseline_avg.append(b_avg)

        for key, _, _ in active_series:
            pairs = collect_trace_pairs(
                ipc_by_label[key],
                sp_weights,
                config=config_by_label[key],
                workload=workload,
                reference_traces=ref,
            )
            series_pairs[key][workload] = pairs
            if not pairs:
                series_avg[key].append(float("nan"))
                series_normalized[key].append(float("nan"))
                continue

            if key in optional_configs and len(pairs) != len(bp):
                common = {cid for cid, _, _ in pairs}
                base_common = collect_trace_pairs(
                    baseline_ipc,
                    sp_weights,
                    config=args.baseline_config,
                    workload=workload,
                    reference_traces=common,
                )
                b_ref = weighted_avg(base_common)
            else:
                b_ref = b_avg

            avg = weighted_avg(pairs)
            series_avg[key].append(avg)
            if b_ref and not math.isnan(avg):
                series_normalized[key].append(round(avg / b_ref, 4))
            else:
                series_normalized[key].append(float("nan"))

    write_computation_log(
        output_dir / "ipc_computation_log.txt",
        apps,
        baseline_avg,
        series_pairs,
        series_avg,
        series_normalized,
        series=active_series,
    )
    print(f"Detailed computation log written to: {output_dir / 'ipc_computation_log.txt'}")

    header = f"{'App':<20} {'Baseline':>10}"
    for _key, label, _color in active_series:
        header += f" {label:>12}"
    print("\nSummary of Weighted Average IPCs (Normalized to Baseline):\n")
    print(header)
    print("-" * (32 + 13 * len(active_series)))

    summary_rows: list[dict[str, str | float]] = []
    for idx, workload in enumerate(apps):
        row: dict[str, str | float] = {
            "workload": workload,
            "baseline_ipc": baseline_avg[idx],
        }
        norm_parts: list[str] = []
        line = f"{workload:<20} {1.0:>10.2f}"
        for key, label, _color in active_series:
            norm = series_normalized[key][idx]
            row[f"{key}_ipc"] = series_avg[key][idx]
            row[f"{key}_speedup"] = norm
            if math.isnan(norm):
                line += f" {'n/a':>12}"
                norm_parts.append(colored("n/a", "yellow"))
            else:
                line += f" {norm:>12.2f}"
                pct = round(100 * (norm - 1.0), 2)
                norm_parts.append(
                    colored(
                        f"{pct:+.2f}%",
                        "green" if pct > 0 else "red" if pct < 0 else "yellow",
                    )
                )
        print(line + "  " + "  ".join(norm_parts))
        summary_rows.append(row)

    avg_row: dict[str, str | float] = {"workload": "arithmetic_mean", "baseline_ipc": ""}
    for key, _, _ in active_series:
        values = [val for val in series_normalized[key] if not math.isnan(val)]
        avg = sum(values) / len(values) if values else float("nan")
        avg_row[f"{key}_ipc"] = ""
        avg_row[f"{key}_speedup"] = avg
    summary_rows.append(avg_row)

    write_summary_csv(output_dir / "ipc_summary.csv", summary_rows)
    plot_speedup_bars(
        apps,
        series_normalized,
        output_dir,
        series=active_series,
        file_prefix=args.file_prefix,
    )

    print("\nIPC comparison plots saved as:")
    print(f"  - {output_dir / f'{args.file_prefix}-labeled.png'}")
    print(f"  - {output_dir / f'{args.file_prefix}-labeled.pdf'}")
    print(f"  - {output_dir / f'{args.file_prefix}.png'}")
    print(f"  - {output_dir / f'{args.file_prefix}.pdf'}")


if __name__ == "__main__":
    main()
