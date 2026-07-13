#!/usr/bin/env python3
"""Simpoint-weighted IPC comparison: I-Fuse vs ideal fusion, normalized to baseline.

Expects scarab-infra simulation results under:
  {simulations-root}/baseline/baseline/datacenter/datacenter/<workload>/<cluster_id>/
  {simulations-root}/ideal-fusion/ideal-fusion/datacenter/datacenter/...
  {simulations-root}/ifuse/ifuse/datacenter/datacenter/...

Example:
  python hpca2027-main-graphs/plot_ipc.py
  python hpca2027-main-graphs/plot_ipc.py --simulations-root /users/deepmish/scarab/src/simulations

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ipc.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab-infra/hpca2027-main-graphs/output
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
SCRIPTS_DIR = SCARAB_INFRA_ROOT / "scripts"
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


GAP_WORKLOADS = ["bc", "bfs", "dfs", "pagerank", "sssp_ego_fb"]
AGENTIC_WORKLOADS = ["feedsim", "langchain_web", "rag_haystack", "tao"]
DATABASE_WORKLOADS = ["mongodb", "postgres"]

SIMPOINT_WORKLOADS = GAP_WORKLOADS + AGENTIC_WORKLOADS + DATABASE_WORKLOADS

IFUSE_COLOR = "#009900"
IDEAL_FUSION_COLOR = "#000000"
MAROON_COLOR = "#060771"
ARROW_THRESHOLD = 0.54

DEFAULT_TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
DEFAULT_SUITE = "datacenter"
DEFAULT_SUBSUITE = "datacenter"
DEFAULT_SIMULATIONS_ROOT = Path("/users/deepmish/scarab/src/simulations")

DEFAULT_BASELINE_DIR = DEFAULT_SIMULATIONS_ROOT / "baseline"
DEFAULT_IFUSE_DIR = DEFAULT_SIMULATIONS_ROOT / "ifuse"
DEFAULT_IDEAL_DIR = DEFAULT_SIMULATIONS_ROOT / "ideal-fusion"

DEFAULT_BASELINE_CONFIG = "baseline"
DEFAULT_IFUSE_CONFIG = "ifuse"
DEFAULT_IDEAL_CONFIG = "ideal-fusion"


def rename_workload(workload: str) -> str:
    mapping = {
        "bc": "BC",
        "bfs": "BFS",
        "dfs": "DFS",
        "pagerank": "PR",
        "sssp_ego_fb": "SSSP",
        "feedsim": "FeedSim",
        "langchain_web": "LangChain",
        "mongodb": "MongoDB",
        "postgres": "Postgres",
        "rag_haystack": "RAG",
        "tao": "Tao",
    }
    return mapping.get(workload, workload)


def optional_stats_csv(experiment_dir: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    primary = experiment_dir / "collected_stats.csv"
    return primary if primary.is_file() else None


def load_simpoint_trace_weights(
    trace_root: Path,
    workloads: list[str],
) -> dict[tuple[str, str], float]:
    weights: dict[tuple[str, str], float] = {}
    for workload in workloads:
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
    """Resolve simpoint dir for standard or flat experiment layouts."""
    candidates = [
        experiment_dir / config / suite / subsuite / workload / cluster_id,
        experiment_dir / suite / subsuite / workload / cluster_id,
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
) -> tuple[set[str], dict[str, set[str]]]:
    """Return (complete workloads, baseline reference trace ids per workload)."""
    complete_apps: set[str] = set()
    reference_by_workload: dict[str, set[str]] = {}
    all_missing: dict[str, dict[str, list[str]]] = defaultdict(dict)

    baseline_dir, baseline_config = directories["baseline"]

    with report_path.open("w") as rpt:
        rpt.write("=" * 100 + "\n")
        rpt.write("SIMPOINT COVERAGE REPORT\n")
        rpt.write("Reference: baseline — all other directories checked against it.\n")
        rpt.write("=" * 100 + "\n\n")

        for workload in workloads:
            rpt.write(f"\n{'=' * 80}\n")
            rpt.write(f"APP: {workload}\n")
            rpt.write(f"{'=' * 80}\n")

            reference_traces = reference_traces_for_workload(
                baseline_dir,
                baseline_config,
                workload,
                sp_weights,
                suite=suite,
                subsuite=subsuite,
            )
            if not reference_traces:
                rpt.write(f"  !! No baseline simpoints for {workload} — skipping\n")
                all_missing[workload]["baseline"] = ["NO BASELINE SIMPOINTS"]
                continue

            reference_by_workload[workload] = reference_traces
            rpt.write(f"  Reference traces from baseline: {sorted(reference_traces)}\n")
            rpt.write(f"  Total reference traces: {len(reference_traces)}\n")
            rpt.write(f"\n  [baseline]  ({baseline_dir})\n")
            rpt.write(
                f"    REFERENCE - {len(reference_traces)} trace(s): "
                f"{sorted(reference_traces)}\n"
            )

            app_complete = True
            for label, (exp_dir, config) in directories.items():
                if label == "baseline":
                    continue
                rpt.write(f"\n  [{label}]  ({exp_dir})\n")
                found: set[str] = set()
                for cid in reference_traces:
                    sim_dir = find_simpoint_dir(
                        exp_dir, config, workload, cid,
                        suite=suite, subsuite=subsuite,
                    )
                    if sim_dir and ipc_from_sim_dir(sim_dir) is not None:
                        found.add(cid)
                missing = reference_traces - found
                if missing:
                    app_complete = False
                    rpt.write(f"    MISSING ({len(missing)}): {sorted(missing)}\n")
                    all_missing[workload][label] = sorted(missing)
                else:
                    rpt.write(f"    OK  - All {len(reference_traces)} trace(s) present.\n")
                rpt.write(f"    Found: {sorted(found)}\n")

            if app_complete:
                complete_apps.add(workload)
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
    baseline_pairs: dict[str, list[tuple[str, float, float]]],
    ifuse_pairs: dict[str, list[tuple[str, float, float]]],
    ideal_pairs: dict[str, list[tuple[str, float, float]]],
    baseline_avg: list[float],
    ifuse_avg: list[float],
    ideal_avg: list[float],
    ifuse_normalized: list[float],
    ideal_normalized: list[float],
) -> None:
    with path.open("w") as log:
        log.write("=" * 100 + "\n")
        log.write("DETAILED IPC COMPUTATION LOG\n")
        log.write("=" * 100 + "\n\n")

        for idx, workload in enumerate(workloads):
            log.write(f"\n{'=' * 80}\n")
            log.write(f"APP: {workload}\n")
            log.write(f"{'=' * 80}\n")

            for label, pairs, avgs in [
                ("BASELINE", baseline_pairs[workload], baseline_avg[idx]),
                ("IFUSE", ifuse_pairs[workload], ifuse_avg[idx]),
                ("IDEAL FUSION", ideal_pairs[workload], ideal_avg[idx]),
            ]:
                log.write(f"\n--- {label} ---\n")
                log.write(f"Number of traces: {len(pairs)}\n")
                total_weight = sum(w for _, _, w in pairs)
                log.write(f"Total weight: {total_weight:.6f}\n")
                log.write("Traces (trace_id, IPC, Weight, IPC*Weight):\n")
                for trace_id, ipc_val, weight in pairs:
                    log.write(
                        f"  Trace {trace_id}: IPC={ipc_val:.6f}, "
                        f"Weight={weight:.6f}, Contribution={ipc_val * weight:.6f}\n"
                    )
                log.write(f"Weighted Average IPC: {avgs:.6f}\n")

            b = baseline_avg[idx]
            h = ifuse_avg[idx]
            i = ideal_avg[idx]
            log.write(f"\n--- SUMMARY FOR {workload} ---\n")
            log.write(f"Baseline:     {b:.6f}\n")
            log.write(f"IFuse:        {h:.6f} (normalized: {h / b:.4f})\n")
            log.write(f"Ideal Fusion: {i:.6f} (normalized: {i / b:.4f})\n")

        log.write(f"\n\n{'=' * 100}\n")
        log.write("NORMALIZED VALUES (to Baseline)\n")
        log.write("=" * 100 + "\n")
        for idx, workload in enumerate(workloads):
            log.write(f"\n{workload}:\n")
            log.write("  Baseline normalized: 1.0000\n")
            log.write(f"  IFuse normalized:    {ifuse_normalized[idx]:.4f}\n")
            log.write(f"  Ideal normalized:    {ideal_normalized[idx]:.4f}\n")
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


def plot_speedup_bars(
    workloads: list[str],
    ifuse_normalized: list[float],
    ideal_normalized: list[float],
    output_dir: Path,
) -> None:
    import matplotlib.pyplot as plt

    ifuse_pct = [(val - 1.0) * 100.0 for val in ifuse_normalized]
    ideal_pct = [(val - 1.0) * 100.0 for val in ideal_normalized]

    ifuse_arithmetic_mean = sum(ifuse_normalized) / len(ifuse_normalized)
    ideal_arithmetic_mean = sum(ideal_normalized) / len(ideal_normalized)

    ifuse_pct.append((ifuse_arithmetic_mean - 1.0) * 100.0)
    ideal_pct.append((ideal_arithmetic_mean - 1.0) * 100.0)

    display_apps = [rename_workload(wl) for wl in workloads] + ["Average"]
    x = list(range(len(display_apps)))
    width = 0.18

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))

    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ifuse_edge_colors = ["red" if val < 0 else "black" for val in ifuse_pct]
    ifuse_edge_widths = [2.5 if val < 0 else 1.0 for val in ifuse_pct]

    ax.bar(
        [i - 0.5 * width for i in x],
        ifuse_pct,
        width,
        label="I-Fuse",
        color=IFUSE_COLOR,
        edgecolor=ifuse_edge_colors,
        linewidth=ifuse_edge_widths,
        zorder=3,
    )
    ax.bar(
        [i + 0.5 * width for i in x],
        ideal_pct,
        width,
        label="Ideal fusion",
        color=IDEAL_FUSION_COLOR,
        edgecolor="black",
        linewidth=1.0,
        zorder=3,
    )

    offsets = [-0.5 * width, 0.5 * width]
    colors = [IFUSE_COLOR, IDEAL_FUSION_COLOR]
    pct_sets = [ifuse_pct, ideal_pct]
    for i in range(len(display_apps)):
        for bar_offset, color, pct_list in zip(offsets, colors, pct_sets):
            val = pct_list[i]
            if 0 <= val < ARROW_THRESHOLD:
                ax.annotate(
                    "",
                    xy=(i + bar_offset, 0),
                    xytext=(i + bar_offset, 5.5),
                    arrowprops=dict(
                        arrowstyle="->", color=color, lw=1.5, mutation_scale=12
                    ),
                    zorder=10,
                )
                ax.text(
                    i + bar_offset - 0.12,
                    5.5,
                    f"{val:.1f}",
                    ha="center",
                    va="bottom",
                    fontsize=24,
                    fontfamily="serif",
                    color=color,
                    zorder=10,
                )

    if len(display_apps) > 1:
        separator_x = len(display_apps) - 1.5
        ax.axvline(
            x=separator_x,
            color=MAROON_COLOR,
            linestyle="--",
            alpha=0.8,
            linewidth=2.5,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(display_apps, rotation=45, ha="right", fontsize=26, fontfamily="serif")
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    ax.set_ylabel(
        "Speedup (%) \n(normalized to no-fusion)",
        fontsize=26,
        fontfamily="serif",
    )
    ylim = _ylim_with_bar_label_headroom(_ylim_speedup_pct_auto(ifuse_pct, ideal_pct))
    ax.set_ylim(ylim[0], ylim[1])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="y", labelsize=20)
    for label in ax.get_yticklabels():
        label.set_fontfamily("serif")

    legend = ax.legend(
        frameon=True, fancybox=False, shadow=False, loc="upper left", fontsize=26, edgecolor="black"
    )
    legend.get_frame().set_linewidth(2.0)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(0.95)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    plt.subplots_adjust(top=1.15)

    for stem in ("ipc",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)

    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot simpoint-weighted I-Fuse vs ideal fusion IPC speedup vs baseline."
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-stats-csv", type=Path, default=None)
    parser.add_argument("--ifuse-stats-csv", type=Path, default=None)
    parser.add_argument("--ideal-fusion-stats-csv", type=Path, default=None)
    parser.add_argument("--baseline-config", default=DEFAULT_BASELINE_CONFIG)
    parser.add_argument("--ifuse-config", default=DEFAULT_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: hpca2027-main-graphs/output)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or (sim_root / "baseline")
    ifuse_dir = args.ifuse_dir or (sim_root / "ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion")

    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    if not workloads:
        raise SystemExit("No workloads left after exclusions.")

    output_dir = args.output_dir or (GRAPH_DIR / "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    baseline_csv = optional_stats_csv(baseline_dir, args.baseline_stats_csv)
    ifuse_csv = optional_stats_csv(ifuse_dir, args.ifuse_stats_csv)
    ideal_csv = optional_stats_csv(ideal_dir, args.ideal_fusion_stats_csv)

    print("Loading IPC (collected_stats.csv + sim directories)...")
    print(f"  baseline:     {baseline_dir} (config={args.baseline_config})")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    baseline_ipc = load_experiment_ipc(
        baseline_dir, args.baseline_config, sp_weights, workloads,
        stats_csv=baseline_csv, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE,
        label="baseline",
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

    directories = {
        "baseline": (baseline_dir, args.baseline_config),
        "ifuse": (ifuse_dir, args.ifuse_config),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config),
    }
    complete_apps, reference_by_workload = check_simpoint_coverage(
        directories,
        workloads,
        sp_weights,
        suite=DEFAULT_SUITE,
        subsuite=DEFAULT_SUBSUITE,
        report_path=output_dir / "simpoint_coverage_report.txt",
    )
    if not complete_apps:
        raise SystemExit(
            "No apps have complete simpoint files across baseline, ifuse, and ideal fusion."
        )

    apps = [wl for wl in workloads if wl in complete_apps]

    baseline_pairs: dict[str, list[tuple[str, float, float]]] = {}
    ifuse_pairs: dict[str, list[tuple[str, float, float]]] = {}
    ideal_pairs: dict[str, list[tuple[str, float, float]]] = {}
    baseline_avg: list[float] = []
    ifuse_avg: list[float] = []
    ideal_avg: list[float] = []

    for workload in apps:
        ref = reference_by_workload[workload]
        bp = collect_trace_pairs(
            baseline_ipc, sp_weights, config=args.baseline_config,
            workload=workload, reference_traces=ref,
        )
        ip = collect_trace_pairs(
            ifuse_ipc, sp_weights, config=args.ifuse_config,
            workload=workload, reference_traces=ref,
        )
        idp = collect_trace_pairs(
            ideal_ipc, sp_weights, config=args.ideal_fusion_config,
            workload=workload, reference_traces=ref,
        )
        baseline_pairs[workload] = bp
        ifuse_pairs[workload] = ip
        ideal_pairs[workload] = idp
        baseline_avg.append(weighted_avg(bp))
        ifuse_avg.append(weighted_avg(ip))
        ideal_avg.append(weighted_avg(idp))

    ifuse_normalized = [
        round(i / b, 4) if b and not math.isnan(i) else 0.0
        for i, b in zip(ifuse_avg, baseline_avg)
    ]
    ideal_normalized = [
        round(i / b, 4) if b and not math.isnan(i) else 0.0
        for i, b in zip(ideal_avg, baseline_avg)
    ]

    write_computation_log(
        output_dir / "ipc_computation_log.txt",
        apps,
        baseline_pairs,
        ifuse_pairs,
        ideal_pairs,
        baseline_avg,
        ifuse_avg,
        ideal_avg,
        ifuse_normalized,
        ideal_normalized,
    )
    print(f"Detailed computation log written to: {output_dir / 'ipc_computation_log.txt'}")

    print("\nSummary of Weighted Average IPCs (Normalized to Baseline):\n")
    print(
        f"{'App':<20} {'Baseline':>10} {'IFuse':>10} {'Ideal':>10} "
        f"{'IFuse Spd':>12} {'Ideal Spd':>12}"
    )
    print("-" * 90)
    summary_rows: list[dict[str, str | float]] = []
    for workload, b_avg, i_avg, id_avg, i_norm, id_norm in zip(
        apps, baseline_avg, ifuse_avg, ideal_avg, ifuse_normalized, ideal_normalized
    ):
        ifuse_pct = round(100 * (i_norm - 1.0), 2)
        ideal_pct = round(100 * (id_norm - 1.0), 2)
        if_change = colored(
            f"{ifuse_pct:+.2f}%",
            "green" if ifuse_pct > 0 else "red" if ifuse_pct < 0 else "yellow",
        )
        ideal_change = colored(
            f"{ideal_pct:+.2f}%",
            "green" if ideal_pct > 0 else "red" if ideal_pct < 0 else "yellow",
        )
        print(
            f"{workload:<20} {1.0:>10.2f} {i_norm:>10.2f} {id_norm:>10.2f} "
            f"{if_change:>12} {ideal_change:>12}"
        )
        summary_rows.append(
            {
                "workload": workload,
                "baseline_ipc": b_avg,
                "ifuse_ipc": i_avg,
                "ideal_fusion_ipc": id_avg,
                "ifuse_speedup": i_norm,
                "ideal_fusion_speedup": id_norm,
            }
        )

    ifuse_arithmetic_mean = sum(ifuse_normalized) / len(ifuse_normalized)
    ideal_arithmetic_mean = sum(ideal_normalized) / len(ideal_normalized)
    summary_rows.append(
        {
            "workload": "arithmetic_mean",
            "baseline_ipc": "",
            "ifuse_ipc": "",
            "ideal_fusion_ipc": "",
            "ifuse_speedup": ifuse_arithmetic_mean,
            "ideal_fusion_speedup": ideal_arithmetic_mean,
        }
    )

    write_summary_csv(output_dir / "ipc_summary.csv", summary_rows)
    plot_speedup_bars(apps, ifuse_normalized, ideal_normalized, output_dir)

    print("\nIPC comparison plots saved as:")
    print(f"  - {output_dir / 'ipc.png'}")
    print(f"  - {output_dir / 'ipc.pdf'}")
    print(f"  - {output_dir / 'ipc.eps'}")


if __name__ == "__main__":
    main()
