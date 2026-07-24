#!/usr/bin/env python3
"""Plot Scarab backend-bound TopDown percentages (Figure 1 style).

Per-simpoint top-down percentages are computed from raw slot counters, then
combined into one value per workload using SimPoint cluster weights from the
scarab-infra workload DB. The plot uses a two-level hierarchical x-axis:
benchmark suite (GAP, Agentic, DCPerf, Database, …) and short application
names (BC, CD, …). Only backend-bound stalls are shown. Workloads below the
evaluation threshold are omitted from the figure; the Average bar is the mean
of the plotted (evaluated) workloads only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
MAIN_GRAPHS = SCARAB_INFRA_ROOT / "hpca2027-main-graphs"
if str(MAIN_GRAPHS) not in sys.path:
    sys.path.insert(0, str(MAIN_GRAPHS))

from plot_ipc import (  # noqa: E402
    FONT_FAMILY,
    IPC_AXIS_LABEL_FONT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    rename_workload,
)


WORKLOAD_LABELS = {
    "bc": "betweenness centrality",
    "bfs": "breadth first search",
    "cc": "connected components",
    "connected_components": "connected components",
    "cd": "community detection",
    "community": "community detection",
    "chemcrow": "chemcrow",
    "dfs": "depth first search",
    "feedsim": "feedsim",
    "langchain_web": "langchain web",
    "mongodb": "mongodb",
    "mysql": "mysql",
    "memcached": "memcached",
    "pagerank": "pagerank",
    "postgres": "postgres",
    "rag_haystack": "rag haystack",
    "redis": "redis",
    "sssp_ego_fb": "single source shortest path",
    "swe_agent": "swe-agent",
    "tao": "taobench",
    "tc": "triangle counting",
    "toolformer": "toolformer",
    "django": "django",
    "videotranscode": "videotranscode",
    "appworld": "AppWorld",
    "core_bench": "Core Bench",
    "mlgym_fmnist": "MLGym FMNIST",
    "terminal_bench": "TerminalBench",
    "rocksdb": "RocksDB",
    "duckdb": "DuckDB",
    "leveldb": "LevelDB",
}

# Level-1 x-axis groups (benchmark suite) and level-2 short application labels.
WORKLOAD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "GAP",
        (
            "bc",
            "bfs",
            "dfs",
            "pagerank",
            "sssp_ego_fb",
        ),
    ),
    (
        "Agentic",
        (
            "appworld",
            "core_bench",
            "mlgym_fmnist",
            "terminal_bench",
        ),
    ),
    ("Database", ("duckdb", "leveldb", "rocksdb")),
)

WORKLOAD_SHORT_LABELS = {
    "bc": "BC",
    "bfs": "BFS",
    "cc": "CC",
    "connected_components": "CC",
    "cd": "CD",
    "community": "CD",
    "dfs": "DFS",
    "pagerank": "PR",
    "sssp_ego_fb": "SSSP",
    "tc": "TC",
    "langchain_web": "LangChain",
    "rag_haystack": "RAG",
    "swe_agent": "SWE",
    "django": "Django",
    "feedsim": "FeedSim",
    "tao": "Tao",
    "videotranscode": "VT",
    "mongodb": "MongoDB",
    "postgres": "Postgres",
    "appworld": "AppWorld",
    "core_bench": "CoreBench",
    "mlgym_fmnist": "MLGym",
    "terminal_bench": "TerminalBench",
    "rocksdb": "RocksDB",
    "duckdb": "DuckDB",
    "leveldb": "LevelDB",
}

WORKLOAD_TO_SUITE: dict[str, str] = {
    workload: suite
    for suite, members in WORKLOAD_GROUPS
    for workload in members
}

# Incomplete / omitted from the evaluation figure.
SKIP_WORKLOADS = frozenset(
    {
        "community",
        "connected_components",
        "cd",
        "cc",
    }
)

STAT_NAMES = {
    "total": "TOPDOWN_TOTAL_SLOTS_count",
    "issued": "TOPDOWN_ISSUED_SLOTS_count",
    "retired": "TOPDOWN_RETIRED_SLOTS_count",
    "fetch_bubbles": "TOPDOWN_FETCH_BUBBLES_SLOTS_count",
    "recovery_bubbles": "TOPDOWN_RECOVERY_BUBBLES_SLOTS_count",
}

# Stacked bar order: bottom → top (Backend bound at bottom)
METRICS = [
    ("Backend bound", "#ff7f00"),
    ("Frontend bound", "#4c78a8"),
    ("Bad speculation", "#984ea3"),
    ("Retiring", "#54a24b"),
]


def read_stats(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    wanted = set(STAT_NAMES.values())
    with path.open(newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 3:
                continue
            name = row[0].strip()
            if name in wanted:
                values[name] = float(row[2].strip())

    missing = sorted(wanted - values.keys())
    if missing:
        raise KeyError(f"{path} missing required stats: {', '.join(missing)}")
    return values


def infer_workload(path: Path, root: Path) -> str:
    parts = path.relative_to(root).parts
    for part in parts:
        if part in WORKLOAD_LABELS:
            return part
    if len(parts) >= 3 and parts[-2].isdigit():
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return path.parent.name


def percentages(counters: dict[str, float]) -> dict[str, float]:
    total = counters["total"]
    if total <= 0:
        raise ValueError("non-positive TOPDOWN_TOTAL_SLOTS")

    frontend = counters["fetch_bubbles"] / total * 100.0
    bad_spec = (
        (counters["issued"] - counters["retired"] + counters["recovery_bubbles"])
        / total
        * 100.0
    )
    retiring = counters["retired"] / total * 100.0
    backend = 100.0 - frontend - bad_spec - retiring
    return {
        "Frontend bound": frontend,
        "Bad speculation": bad_spec,
        "Retiring": retiring,
        "Backend bound": backend,
        "Topdown sum": frontend + bad_spec + retiring + backend,
    }


def load_weights(
    db_path: Path, suite: str, subsuite: str
) -> dict[str, dict[str, float]]:
    """Return workload -> cluster_id -> weight.

    The primary suite/subsuite (e.g. datacenter/datacenter) is required; other
    suites in the DB (e.g. dcperf) are merged in as well so one plot can span
    suites. The primary section wins on workload-name collisions.
    """
    data = json.loads(db_path.read_text())
    try:
        primary = data[suite][subsuite]
    except KeyError as exc:
        raise SystemExit(
            f"{db_path}: missing workload DB section {suite}/{subsuite}"
        ) from exc

    def section_weights(workloads) -> dict[str, dict[str, float]]:
        return {
            workload: {
                str(sp["cluster_id"]): float(sp["weight"])
                for sp in entry["simpoints"]
            }
            for workload, entry in workloads.items()
            if isinstance(entry, dict) and "simpoints" in entry
        }

    merged: dict[str, dict[str, float]] = {}
    for suite_name, subsuites in data.items():
        if not isinstance(subsuites, dict):
            continue
        for subsuite_name, workloads in subsuites.items():
            if not isinstance(workloads, dict):
                continue
            if (suite_name, subsuite_name) == (suite, subsuite):
                continue
            merged.update(section_weights(workloads))
    merged.update(section_weights(primary))
    return merged


def find_trace_root(app_dir: Path) -> Path | None:
    if (app_dir / "simpoints").is_dir():
        return app_dir
    for child in sorted(app_dir.iterdir()):
        if child.is_dir() and (child / "simpoints").is_dir():
            return child
    return None


def installed_trace_workloads(traces_dir: Path) -> set[str]:
    """Workload names with SimPoint bundles under simpoint_traces."""
    installed: set[str] = set()
    if not traces_dir.is_dir():
        return installed

    for candidate in (traces_dir, traces_dir / "datacenter" / "datacenter"):
        if not candidate.is_dir():
            continue
        for app_dir in sorted(candidate.iterdir()):
            if not app_dir.is_dir():
                continue
            if find_trace_root(app_dir) is not None:
                installed.add(app_dir.name)
    return installed


def collect(
    root: Path,
    weights_db: dict[str, dict[str, float]],
    *,
    allowed_workloads: set[str] | None = None,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    per_simpoint: dict[str, list[tuple[str, dict[str, float]]]] = defaultdict(list)
    seen: dict[str, dict[tuple, Path]] = defaultdict(dict)

    for stat_path in sorted(root.rglob("core.stat.0.csv")):
        workload = infer_workload(stat_path, root)
        cluster = stat_path.parent.name
        stats = read_stats(stat_path)
        counters = {key: stats[name] for key, name in STAT_NAMES.items()}

        fingerprint = tuple(counters[key] for key in STAT_NAMES)
        prior = seen[workload].get(fingerprint)
        if prior is not None:
            print(
                f"Warning: {workload} simpoint {cluster} at {stat_path} duplicates "
                f"{prior}; skipping duplicate simpoint."
            )
            continue
        seen[workload][fingerprint] = stat_path
        per_simpoint[workload].append((cluster, counters))

    if not per_simpoint:
        raise SystemExit(f"No core.stat.0.csv files found under {root}")

    rows: list[dict[str, float]] = []
    simpoint_rows: list[dict[str, float]] = []
    for workload, entries in sorted(per_simpoint.items()):
        if workload in SKIP_WORKLOADS:
            continue
        if allowed_workloads is not None and workload not in allowed_workloads:
            continue
        wl_weights = weights_db.get(workload)
        if wl_weights is None:
            raise SystemExit(f"{workload}: no simpoints/weights entry in the workload DB")
        missing = [cluster for cluster, _ in entries if cluster not in wl_weights]
        if missing:
            raise SystemExit(
                f"{workload}: no SimPoint weight for simpoint(s) {', '.join(sorted(missing))}; "
                f"DB lists {sorted(wl_weights)}"
            )

        norm = sum(wl_weights[cluster] for cluster, _ in entries)
        if norm <= 0:
            raise SystemExit(f"{workload}: present SimPoint weights sum to {norm}")

        workload_simpoints: list[dict[str, float]] = []
        for cluster, counters in sorted(entries, key=lambda entry: int(entry[0])):
            pct = percentages(counters)
            simpoint_row = {
                "workload": workload,
                "label": WORKLOAD_LABELS.get(
                    workload, workload.replace("_", " ")
                ),
                "cluster_id": cluster,
                "weight": wl_weights[cluster],
                "normalized_weight": wl_weights[cluster] / norm,
                "total_slots": counters["total"],
                "issued_slots": counters["issued"],
                "retired_slots": counters["retired"],
                "fetch_bubbles_slots": counters["fetch_bubbles"],
                "recovery_bubbles_slots": counters["recovery_bubbles"],
                **pct,
            }
            workload_simpoints.append(simpoint_row)
            simpoint_rows.append(simpoint_row)

        row: dict[str, float] = {
            "workload": workload,
            "label": WORKLOAD_LABELS.get(workload, workload.replace("_", " ")),
            "benchmark_suite": WORKLOAD_TO_SUITE.get(workload, "Other"),
            "simpoints": len(entries),
            "total_slots": sum(counters["total"] for _, counters in entries),
        }
        for metric, _ in METRICS:
            row[metric] = sum(
                simpoint["normalized_weight"] * simpoint[metric]
                for simpoint in workload_simpoints
            )
        row["Topdown sum"] = sum(row[metric] for metric, _ in METRICS)
        rows.append(row)

    if not rows:
        hint = (
            f" (allowed traces: {', '.join(sorted(allowed_workloads))})"
            if allowed_workloads
            else ""
        )
        raise SystemExit(f"No matching workload stats found under {root}{hint}")

    average = {
        "workload": "Average",
        "label": "Average",
        "benchmark_suite": "",
        "simpoints": sum(row["simpoints"] for row in rows),
        "total_slots": sum(row["total_slots"] for row in rows),
    }
    for metric, _ in METRICS:
        average[metric] = sum(row[metric] for row in rows) / len(rows)
    average["Topdown sum"] = sum(average[metric] for metric, _ in METRICS)
    rows.append(average)
    return rows, simpoint_rows


def write_csv(rows: list[dict[str, float]], path: Path) -> None:
    fieldnames = [
        "workload",
        "label",
        "benchmark_suite",
        "simpoints",
        "total_slots",
        "Frontend bound",
        "Bad speculation",
        "Retiring",
        "Backend bound",
        "Topdown sum",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fieldnames})


def write_simpoint_csv(rows: list[dict[str, float]], path: Path) -> None:
    fieldnames = [
        "workload",
        "label",
        "cluster_id",
        "weight",
        "normalized_weight",
        "total_slots",
        "issued_slots",
        "retired_slots",
        "fetch_bubbles_slots",
        "recovery_bubbles_slots",
        "Frontend bound",
        "Bad speculation",
        "Retiring",
        "Backend bound",
        "Topdown sum",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fieldnames})


# Plot styling (aligned with hpca2027-main-graphs/plot_ipc.py)
STANFORD_RED = "#8C1515"
CMU_RED = "#C41230"
BAR_COLOR = "#A81423"  # 50/50 blend of Stanford and CMU cardinal reds
AVERAGE_SEPARATOR_COLOR = "#2A2A2A"
AVERAGE_SEPARATOR_WIDTH = 3.5
BAR_WIDTH = 0.40
FIGSIZE = (24.0, 6.5)
BAR_EDGE_WIDTH = 3.0
DEFAULT_SCARAB_ROOT = Path("/users/deepmish/scarab")
DEFAULT_SIM_ROOT = DEFAULT_SCARAB_ROOT / "src" / "simulations" / "baseline"
DEFAULT_RESULTS_ROOT = DEFAULT_SCARAB_ROOT / "src" / "hpca2027-characterization-results"
DEFAULT_BACKEND_STALLS_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "backend_stalls"
DEFAULT_WEIGHTS_DB = Path(__file__).resolve().parent.parent / "workloads" / "workloads_db.json"
DEFAULT_TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
DEFAULT_EVAL_BACKEND_THRESHOLD = 15.0


def _apply_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
        }
    )


def _tight_x_limits(ax, x_min: float, x_max: float) -> None:
    left_pad = 0.12
    right_pad = 0.12
    half_span = BAR_WIDTH / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


def _workload_rows_for_plot(
    rows: list[dict[str, float]],
    *,
    eval_backend_threshold: float | None,
) -> list[dict[str, float]]:
    by_name = {
        str(row["workload"]): row
        for row in rows
        if row["workload"] != "Average"
    }
    ordered: list[dict[str, float]] = []
    for workload in SIMPOINT_WORKLOADS:
        row = by_name.get(workload)
        if row is None:
            continue
        if (
            eval_backend_threshold is not None
            and float(row["Backend bound"]) < eval_backend_threshold
        ):
            continue
        ordered.append(row)
    if not ordered:
        raise SystemExit("No main-graph workloads to plot.")
    return ordered


def plot(
    rows: list[dict[str, float]],
    out_png: Path,
    out_pdf: Path | None,
    *,
    eval_backend_threshold: float | None = DEFAULT_EVAL_BACKEND_THRESHOLD,
) -> None:
    workload_rows = _workload_rows_for_plot(
        rows, eval_backend_threshold=eval_backend_threshold
    )
    backend_values = [float(row["Backend bound"]) for row in workload_rows]
    average_backend = sum(backend_values) / len(backend_values)

    display_apps = [rename_workload(str(row["workload"])) for row in workload_rows]
    display_apps.append("Average")
    values = backend_values + [average_backend]
    x = list(range(len(display_apps)))

    _apply_plot_style()
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    ax.bar(
        x,
        values,
        BAR_WIDTH,
        color=BAR_COLOR,
        edgecolor="black",
        linewidth=BAR_EDGE_WIDTH,
        zorder=3,
    )

    if len(display_apps) > 1:
        ax.axvline(
            x=len(display_apps) - 1.5,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            linewidth=AVERAGE_SEPARATOR_WIDTH,
            zorder=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        display_apps,
        rotation=45,
        ha="right",
        fontsize=IPC_TICK_FONT,
        fontfamily=FONT_FAMILY,
    )
    for label in ax.get_xticklabels():
        if label.get_text() == "Average":
            label.set_weight("bold")

    _tight_x_limits(ax, x[0], x[-1])

    ax.set_ylabel(
        "Backend bound stalls (%)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    y_max = max(values) if values else 100.0
    ymax = min(100.0, max(10.0, (int(y_max / 10) + 1) * 10))
    ax.set_ylim(0.0, ymax * 1.08)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    plt.subplots_adjust(top=1.12, bottom=0.28)
    fig.savefig(out_png, bbox_inches="tight", dpi=300)
    if out_pdf:
        fig.savefig(out_pdf, bbox_inches="tight", dpi=300)
    plt.close(fig)


def validate(rows: list[dict[str, float]], tolerance: float) -> None:
    for row in rows:
        total = float(row["Topdown sum"])
        if abs(total - 100.0) > tolerance:
            raise ValueError(f"{row['workload']} top-down categories sum to {total:.3f}, not 100")
        if float(row["Backend bound"]) < -tolerance:
            raise ValueError(f"{row['workload']} has negative backend bound: {row['Backend bound']:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SIM_ROOT,
        help="Simulation/stat root to search recursively.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_BACKEND_STALLS_OUTPUT_DIR,
        help="Directory for CSV/PNG/PDF outputs.",
    )
    parser.add_argument(
        "--weights-db",
        type=Path,
        default=DEFAULT_WEIGHTS_DB,
        help="workloads DB JSON (e.g. workloads/workloads_db.json) providing SimPoint cluster weights.",
    )
    parser.add_argument(
        "--traces-dir",
        type=Path,
        default=DEFAULT_TRACE_ROOT,
        help="Only plot workloads with SimPoint bundles under this directory.",
    )
    parser.add_argument("--suite", default="datacenter")
    parser.add_argument("--subsuite", default="datacenter")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF output.")
    parser.add_argument(
        "--eval-backend-threshold",
        type=float,
        default=DEFAULT_EVAL_BACKEND_THRESHOLD,
        metavar="PCT",
        help=(
            "Backend-bound threshold (%%); only workloads at or above this value "
            f"are plotted (default: {DEFAULT_EVAL_BACKEND_THRESHOLD}). "
            "Use a negative value to plot all workloads."
        ),
    )
    parser.add_argument("--sum-tolerance", type=float, default=0.01)
    args = parser.parse_args()

    allowed = installed_trace_workloads(args.traces_dir)
    if not allowed:
        raise SystemExit(f"No SimPoint workloads found under {args.traces_dir}")

    weights = load_weights(args.weights_db, args.suite, args.subsuite)
    rows, simpoint_rows = collect(args.root, weights, allowed_workloads=allowed)
    validate(rows, args.sum_tolerance)

    skipped = sorted(
        {
            infer_workload(path, args.root)
            for path in args.root.rglob("core.stat.0.csv")
        }
        - allowed
        - {"Average"}
    )
    if skipped:
        print(
            "Skipping sim results without installed traces: "
            + ", ".join(skipped)
        )
    print(f"Plotting installed traces: {', '.join(sorted(allowed))}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "topdown_backend_stalls.csv"
    simpoint_csv_path = args.out_dir / "topdown_backend_stalls_per_simpoint.csv"
    png_path = args.out_dir / "backend-stalls.png"
    pdf_path = None if args.no_pdf else args.out_dir / "backend-stalls.pdf"

    write_csv(rows, csv_path)
    write_simpoint_csv(simpoint_rows, simpoint_csv_path)
    threshold = (
        None
        if args.eval_backend_threshold < 0
        else args.eval_backend_threshold
    )
    plot(rows, png_path, pdf_path, eval_backend_threshold=threshold)

    if threshold is not None:
        workload_rows = [row for row in rows if row["workload"] != "Average"]
        evaluated = sorted(
            row["workload"]
            for row in workload_rows
            if float(row["Backend bound"]) >= threshold
        )
        excluded = sorted(
            row["workload"]
            for row in workload_rows
            if float(row["Backend bound"]) < threshold
        )
        print(
            f"\nEvaluation subset (backend bound >= {threshold:.1f}%): "
            f"{len(evaluated)} workload(s)"
        )
        print(f"  evaluated: {', '.join(evaluated) or '(none)'}")
        print(
            f"  excluded:  {', '.join(excluded) or '(none)'}"
        )

    print(f"Wrote {csv_path}")
    print(f"Wrote {simpoint_csv_path}")
    print(f"Wrote {png_path}")
    if pdf_path:
        print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
