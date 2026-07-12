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
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


WORKLOAD_LABELS = {
    "bc": "betweenness centrality",
    "bfs": "breadth first search",
    "cc": "connected components",
    "cd": "community detection",
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
}

# Level-1 x-axis groups (benchmark suite) and level-2 short application labels.
WORKLOAD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GAP", ("bc", "bfs", "cc", "cd", "dfs", "pagerank", "sssp_ego_fb", "tc")),
    ("Agentic", ("langchain_web", "rag_haystack", "swe_agent")),
    ("DCPerf", ("django", "feedsim", "tao", "videotranscode")),
    ("Database", ("mongodb", "postgres")),
    ("SPEC", ()),
)

WORKLOAD_SHORT_LABELS = {
    "bc": "BC",
    "bfs": "BFS",
    "cc": "CC",
    "cd": "CD",
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
}

WORKLOAD_TO_SUITE: dict[str, str] = {
    workload: suite
    for suite, members in WORKLOAD_GROUPS
    for workload in members
}

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


def collect(
    root: Path, weights_db: dict[str, dict[str, float]]
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
            raise SystemExit(
                f"{workload}: {stat_path} and {prior} have identical top-down counters. "
                "Distinct simpoints must not produce identical stats; this usually means the "
                "measured ROI window sat in a shared trace chunk (segment_size/window "
                "mismatch with the trace's chunk_instr_count)."
            )
        seen[workload][fingerprint] = stat_path
        per_simpoint[workload].append((cluster, counters))

    if not per_simpoint:
        raise SystemExit(f"No core.stat.0.csv files found under {root}")

    rows: list[dict[str, float]] = []
    simpoint_rows: list[dict[str, float]] = []
    for workload, entries in sorted(per_simpoint.items()):
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


# Plot styling (aligned with instruction-fusion plot_ipc.py)
CARNEGIE_RED = "#C41230"
CATEGORY_GAP = 1.05
GROUP_GAP = 0.95
SUMMARY_GAP = 1.15
AXIS_FONT = 34
GROUP_FONT = 30
APP_FONT = 28
FIGSIZE = (24.0, 11.9)
BAR_WIDTH = 0.68
SUMMARY_COLUMN_SHADE_FACE = "#c0c0c0"
SUMMARY_COLUMN_SHADE_ALPHA = 0.28
SUMMARY_SEPARATOR_COLOR = "#DC3B23"
GROUP_SEPARATOR_COLOR = "#666666"
SUMMARY_XTICK = "Average"
DEFAULT_EVAL_BACKEND_THRESHOLD = 15.0


def _short_label(workload: str) -> str:
    return WORKLOAD_SHORT_LABELS.get(
        workload, workload.replace("_", " ").upper()
    )


def _order_rows_hierarchical(
    rows: list[dict[str, float]],
) -> tuple[list[dict[str, float]], list[tuple[str, list[dict[str, float]]]], dict[str, float] | None]:
    by_name = {str(row["workload"]): row for row in rows if row["workload"] != "Average"}
    ordered: list[dict[str, float]] = []
    grouped: list[tuple[str, list[dict[str, float]]]] = []
    seen: set[str] = set()

    for suite_name, members in WORKLOAD_GROUPS:
        group_rows = [by_name[wl] for wl in members if wl in by_name]
        if not group_rows:
            continue
        grouped.append((suite_name, group_rows))
        ordered.extend(group_rows)
        seen.update(str(row["workload"]) for row in group_rows)

    leftovers = sorted(
        (row for wl, row in by_name.items() if wl not in seen),
        key=lambda row: str(row["workload"]),
    )
    if leftovers:
        grouped.append(("Other", leftovers))
        ordered.extend(leftovers)

    average = next((row for row in rows if row["workload"] == "Average"), None)
    return ordered, grouped, average


def _shade_summary_column(ax, separator_x: float, average_x: float) -> None:
    """Shade the full Average column from the dashed separator through the bar."""
    ax.axvspan(
        separator_x,
        average_x + BAR_WIDTH / 2.0 + 0.10,
        facecolor=SUMMARY_COLUMN_SHADE_FACE,
        alpha=SUMMARY_COLUMN_SHADE_ALPHA,
        zorder=-1,
        linewidth=0,
        clip_on=True,
    )


def _tight_x_limits(ax, x_min: float, x_max: float) -> None:
    left_pad = 0.18
    right_pad = 0.14
    ax.set_xlim(x_min - BAR_WIDTH / 2 - left_pad, x_max + BAR_WIDTH / 2 + right_pad)
    ax.margins(x=0)


def _add_hierarchical_xaxis(
    ax,
    positions: list[float],
    app_labels: list[str],
    group_spans: list[tuple[float, float, str]],
) -> None:
    from matplotlib.transforms import blended_transform_factory

    ax.set_xticks(positions)
    ax.set_xticklabels(
        app_labels,
        rotation=45,
        ha="right",
        fontsize=APP_FONT,
        fontfamily="serif",
    )
    group_transform = blended_transform_factory(ax.transData, ax.transAxes)
    for x_start, x_end, group_name in group_spans:
        ax.text(
            (x_start + x_end) / 2.0,
            -0.32,
            group_name,
            transform=group_transform,
            ha="center",
            va="top",
            fontsize=GROUP_FONT,
            fontfamily="serif",
            fontweight="bold",
            clip_on=False,
        )


def _filter_evaluated_groups(
    grouped: list[tuple[str, list[dict[str, float]]]],
    threshold: float | None,
    full_average: dict[str, float] | None,
) -> tuple[list[tuple[str, list[dict[str, float]]]], dict[str, float] | None]:
    """Keep only workloads at or above the evaluation threshold."""
    if threshold is None:
        return grouped, full_average

    filtered: list[tuple[str, list[dict[str, float]]]] = []
    evaluated: list[dict[str, float]] = []
    for group_name, group_rows in grouped:
        kept = [
            row for row in group_rows if float(row["Backend bound"]) >= threshold
        ]
        if kept:
            filtered.append((group_name, kept))
            evaluated.extend(kept)

    if not evaluated:
        raise SystemExit(
            f"No workloads meet the evaluation threshold ({threshold:.1f}% backend bound)."
        )

    evaluated_average: dict[str, float] = {
        "workload": "Average",
        "label": "Average",
        "Backend bound": sum(float(row["Backend bound"]) for row in evaluated)
        / len(evaluated),
    }
    return filtered, evaluated_average


def plot(
    rows: list[dict[str, float]],
    out_png: Path,
    out_pdf: Path | None,
    *,
    eval_backend_threshold: float | None = DEFAULT_EVAL_BACKEND_THRESHOLD,
) -> None:
    _, grouped, full_average = _order_rows_hierarchical(rows)
    grouped, average_row = _filter_evaluated_groups(
        grouped, eval_backend_threshold, full_average
    )

    positions: list[float] = []
    values: list[float] = []
    app_labels: list[str] = []
    group_spans: list[tuple[float, float, str]] = []
    group_separators: list[float] = []

    x = 0.0
    for group_idx, (group_name, group_rows) in enumerate(grouped):
        if group_idx > 0:
            group_separators.append(x - GROUP_GAP / 2.0)
        group_start = x
        for row in group_rows:
            backend = float(row["Backend bound"])
            positions.append(x)
            values.append(backend)
            app_labels.append(_short_label(str(row["workload"])))
            x += CATEGORY_GAP
        group_spans.append((group_start, x - CATEGORY_GAP, group_name))
        x += GROUP_GAP

    average_x: float | None = None
    if average_row is not None:
        if positions:
            group_separators.append(x - GROUP_GAP / 2.0)
            x += SUMMARY_GAP
        average_x = x
        positions.append(average_x)
        values.append(float(average_row["Backend bound"]))
        app_labels.append(SUMMARY_XTICK)

    if not positions:
        raise SystemExit("No workloads to plot.")

    fig_width = max(FIGSIZE[0], len(positions) * 0.98 + len(grouped) * 0.65)
    plt.rcParams.update(
        {"font.size": 15, "font.family": "serif", "axes.labelsize": AXIS_FONT}
    )
    fig, ax = plt.subplots(figsize=(fig_width, FIGSIZE[1]))

    summary_separator_x: float | None = None
    if average_x is not None:
        summary_separator_x = average_x - CATEGORY_GAP / 2.0 - SUMMARY_GAP / 2.0
        _shade_summary_column(ax, summary_separator_x, average_x)

    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    ax.bar(
        positions,
        values,
        BAR_WIDTH,
        alpha=1.0,
        color=CARNEGIE_RED,
        edgecolor="black",
        linewidth=1.5,
        zorder=3,
    )

    for sep_x in group_separators:
        ax.axvline(
            x=sep_x,
            color=GROUP_SEPARATOR_COLOR,
            linestyle=":",
            alpha=0.50,
            linewidth=2.0,
            zorder=1,
        )

    if summary_separator_x is not None:
        ax.axvline(
            x=summary_separator_x,
            color=SUMMARY_SEPARATOR_COLOR,
            linestyle="--",
            alpha=0.8,
            linewidth=3.0,
            zorder=2,
        )

    y_max = max(values) if values else 100.0
    ax.set_ylabel(
        "Backend bound (%)",
        fontsize=AXIS_FONT,
        fontfamily="serif",
        labelpad=18,
    )
    ax.set_ylim(0, max(y_max * 1.08, 10.0))
    _add_hierarchical_xaxis(ax, positions, app_labels, group_spans)

    if average_x is not None:
        for tick in ax.get_xticklabels():
            if tick.get_text() == SUMMARY_XTICK:
                tick.set_weight("bold")

    ax.tick_params(axis="x", pad=8)
    ax.tick_params(axis="y", labelsize=AXIS_FONT)
    for tick in ax.get_yticklabels():
        tick.set_fontfamily("serif")
        tick.set_fontsize(AXIS_FONT)

    _tight_x_limits(ax, positions[0], positions[-1])

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    fig.subplots_adjust(bottom=0.32)
    fig.savefig(out_png, bbox_inches="tight", dpi=300, pad_inches=0.06)
    if out_pdf:
        fig.savefig(out_pdf, bbox_inches="tight", dpi=300, pad_inches=0.06)
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
    parser.add_argument("--root", type=Path, required=True, help="Simulation/stat root to search recursively.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for CSV/PNG/PDF outputs.")
    parser.add_argument(
        "--weights-db",
        type=Path,
        required=True,
        help="workloads DB JSON (e.g. workloads/workloads_db.json) providing SimPoint cluster weights.",
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

    weights = load_weights(args.weights_db, args.suite, args.subsuite)
    rows, simpoint_rows = collect(args.root, weights)
    validate(rows, args.sum_tolerance)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "topdown_backend_stalls.csv"
    simpoint_csv_path = args.out_dir / "topdown_backend_stalls_per_simpoint.csv"
    png_path = args.out_dir / "topdown_backend_stalls.png"
    pdf_path = None if args.no_pdf else args.out_dir / "topdown_backend_stalls.pdf"

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
