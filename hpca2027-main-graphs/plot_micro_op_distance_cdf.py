#!/usr/bin/env python3
"""CDF of micro-op distance between dynamic fusible load pairs (ideal fusion pass 1).

Pass-1 candidate CSVs list one row per dynamic fusible LD1/LD2 pair. The last
column is micro_op_distance = load2_micro_op_num - load1_micro_op_num.

This script plots an empirical CDF with:
  x-axis: micro-op distance (log scale)
  y-axis: fraction of pairs whose distance is <= x

Commands:

# Slow path: stream candidate CSVs (one-time / data refresh).
/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_micro_op_distance_cdf.py \
  --candidates-dir /dev/shm/baseline/ideal_fusion_candidates_unbounded \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/micro-op-distance-cdf-unbounded

# Fast path: restyle from cached per-workload CDF CSVs in --output-dir.
/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_micro_op_distance_cdf.py \
  --replot \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/micro-op-distance-cdf-unbounded
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    DEFAULT_RESULTS_ROOT,
    FONT_FAMILY,
    IDEAL_FUSION_COLOR,
    rename_workload,
)

DEFAULT_CANDIDATES_DIR = Path("/dev/shm/baseline/ideal_fusion_candidates")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "micro-op-distance-cdf"

COL_MICRO_OP_DISTANCE = 10
AGGREGATE_LABEL = "All workloads"
AGGREGATE_COLOR = IDEAL_FUSION_COLOR
DEFAULT_NUM_LOG_BINS = 800
DEFAULT_LOG_MAX = 8.0  # log10 ceiling before streaming (10^8 micro-ops)

FIGURE_STEM = "micro-op-distance-cdf"
CDF_CSV_PREFIX = "micro-op-distance-cdf"
SUMMARY_CSV_NAME = "micro-op-distance-summary.csv"
# Backward-compatible names from older runs.
LEGACY_FIGURE_STEM = "micro_op_distance_cdf"
LEGACY_CDF_CSV_PREFIX = "micro_op_distance_cdf"

# Axis fonts stay paper-readable; legend stays compact inside the axes.
PLOT_LABEL_FONT = 26
PLOT_TICK_FONT = 22
PLOT_LEGEND_FONT = 15
NOTO_SERIF_FONT_DIR = Path.home() / ".local/share/fonts" / "noto-serif"
_noto_serif_registered = False

# Paper subset / plot order.
DEFAULT_WORKLOADS = [
    "bfs",
    "dfs",
    "pagerank",
    "corebench",
    "appworld",
    "terminal_bench",
    "clickhouse",
    "duckdb",
    "leveldb",
    "memcached",
]

# Directory-name aliases (simpoint release vs workloads_db naming).
WORKLOAD_DIR_ALIASES: dict[str, tuple[str, ...]] = {
    "corebench": ("corebench", "core_bench"),
    "core_bench": ("core_bench", "corebench"),
}

# Per-app colors (user palette + LevelDB / Memcached).
WORKLOAD_COLORS: dict[str, str] = {
    "bfs": "#017E7C",
    "dfs": "#8F993E",
    "pagerank": "#016895",
<<<<<<< HEAD
    "appworld": "#E98300",
    "corebench": "#C74632",
=======
    "corebench": "#C74632",
    "core_bench": "#C74632",
    "appworld": "#E98300",
>>>>>>> 81611a5 (Polish micro-op distance CDF for paper apps and hyphenated outputs.)
    "terminal_bench": "#620059",
    "clickhouse": "#795548",
    "duckdb": "#9475BD",
    "leveldb": "#FEC51D",
    "memcached": "#00796B",
    "rocksdb": "#FEC51D",
}
LEGEND_EDGE_WIDTH = 0.8
LEGEND_FRAME_WIDTH = 0.8
LEGEND_FRAME_COLOR = "#000000"
# Paper figure size; legend sits in the empty lower-right of the axes.
PLOT_FIGSIZE = (10.0, 4.2)
# (CDF fraction, label, label offset in points from intersection)
CDF_REFERENCE_LEVELS: tuple[tuple[float, str, tuple[int, int]], ...] = (
    (0.80, "p80", (6, -10)),
    (0.95, "p95", (6, -10)),
)
CDF_REFERENCE_COLOR = "#C74632"
CDF_REFERENCE_LABEL_FONT = 14

WORKLOAD_DISPLAY_NAMES: dict[str, str] = {
    "corebench": "CoreBench",
    "memcached": "Memcached",
}


@dataclass
class DistanceHistogram:
    counts: list[int]
    log_min: float
    log_max: float
    total: int
    max_distance: int

    @property
    def num_bins(self) -> int:
        return len(self.counts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot CDF of micro-op distance for ideal-fusion candidate pairs."
    )
    parser.add_argument(
        "--candidates-dir",
        type=Path,
        default=DEFAULT_CANDIDATES_DIR,
        help="Pass-1 candidate root: {workload}/{cluster_id}.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for figures and summary CSV.",
    )
    parser.add_argument(
        "--workloads",
        nargs="*",
        default=DEFAULT_WORKLOADS,
        help=(
            "Workload subset to plot (default: BFS/DFS/PR/CoreBench/AppWorld/"
            "TerminalBench/ClickHouse/DuckDB/LevelDB/Memcached)."
        ),
    )
    parser.add_argument(
        "--all-workloads",
        action="store_true",
        help="Plot every directory under candidates-dir (overrides --workloads).",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Also draw an aggregate curve across all workloads.",
    )
    parser.add_argument(
        "--num-bins",
        type=int,
        default=DEFAULT_NUM_LOG_BINS,
        help="Number of log-spaced histogram bins used while streaming CSV rows.",
    )
    parser.add_argument(
        "--log-max",
        type=float,
        default=DEFAULT_LOG_MAX,
        help="Initial log10 ceiling for binning (expanded if data exceeds it).",
    )
    parser.add_argument(
        "--replot",
        action="store_true",
        help=(
            "Skip streaming candidates; load cached micro-op-distance-cdf-*.csv "
            "from --output-dir and only redraw the figure (fast style iteration)."
        ),
    )
    return parser.parse_args(argv)


def resolve_workload_dir(candidates_dir: Path, workload: str) -> Path:
    candidates = WORKLOAD_DIR_ALIASES.get(workload, (workload,))
    for name in candidates:
        wl_dir = candidates_dir / name
        if wl_dir.is_dir():
            return wl_dir
    tried = ", ".join(str(candidates_dir / name) for name in candidates)
    raise SystemExit(f"Missing workload directory for {workload!r} (tried: {tried})")


def discover_candidate_files(
    candidates_dir: Path, workloads: list[str] | None
) -> list[tuple[str, str, Path]]:
    tasks: list[tuple[str, str, Path]] = []
    if workloads:
        workload_names = workloads
    else:
        workload_names = sorted(
            entry.name
            for entry in candidates_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )

    for workload in workload_names:
        wl_dir = resolve_workload_dir(candidates_dir, workload)
        # Keep the requested/canonical name so colors and ordering stay stable.
        for csv_path in sorted(wl_dir.glob("*.csv")):
            tasks.append((workload, csv_path.stem, csv_path))
    if not tasks:
        raise SystemExit(f"No candidate CSVs found under {candidates_dir}")
    return tasks


def workload_display_name(workload: str) -> str:
    if workload in WORKLOAD_DISPLAY_NAMES:
        return WORKLOAD_DISPLAY_NAMES[workload]
    aliased = {
        "corebench": "core_bench",
    }.get(workload, workload)
    return rename_workload(aliased)


def empty_histogram(log_max: float, num_bins: int) -> DistanceHistogram:
    return DistanceHistogram(
        counts=[0] * num_bins,
        log_min=0.0,
        log_max=log_max,
        total=0,
        max_distance=0,
    )


def _bin_index(distance: int, hist: DistanceHistogram) -> int:
    if distance <= 0:
        return -1
    log_d = math.log10(distance)
    if log_d >= hist.log_max:
        return hist.num_bins - 1
    if log_d <= hist.log_min:
        return 0
    span = hist.log_max - hist.log_min
    idx = int((log_d - hist.log_min) / span * hist.num_bins)
    return min(idx, hist.num_bins - 1)


def add_distance(hist: DistanceHistogram, distance: int) -> None:
    idx = _bin_index(distance, hist)
    if idx < 0:
        return
    hist.counts[idx] += 1
    hist.total += 1
    if distance > hist.max_distance:
        hist.max_distance = distance
        needed_log = math.log10(distance) + 0.01
        if needed_log > hist.log_max:
            hist.log_max = needed_log


def stream_histograms(
    tasks: list[tuple[str, str, Path]],
    log_max: float,
    num_bins: int,
) -> tuple[DistanceHistogram, dict[str, DistanceHistogram]]:
    aggregate = empty_histogram(log_max, num_bins)
    per_workload: dict[str, DistanceHistogram] = defaultdict(
        lambda: empty_histogram(log_max, num_bins)
    )
    rows_read = 0

    for workload, _cluster_id, csv_path in tasks:
        wl_hist = per_workload[workload]
        with csv_path.open() as fh:
            fh.readline()  # header
            for line in fh:
                parts = line.split(",")
                if len(parts) <= COL_MICRO_OP_DISTANCE:
                    continue
                try:
                    distance = int(parts[COL_MICRO_OP_DISTANCE])
                except ValueError:
                    continue
                rows_read += 1
                add_distance(aggregate, distance)
                add_distance(wl_hist, distance)

    print(f"Read {rows_read:,} dynamic candidate rows", flush=True)
    print(
        f"Aggregate max distance: {aggregate.max_distance:,} micro-ops",
        flush=True,
    )
    return aggregate, dict(per_workload)


def histogram_to_cdf(hist: DistanceHistogram) -> tuple[list[float], list[float]]:
    if hist.total <= 0:
        return [], []

    log_span = hist.log_max - hist.log_min
    xs: list[float] = [1.0]
    ys: list[float] = [0.0]
    cumulative = 0.0

    for idx, count in enumerate(hist.counts):
        if count <= 0:
            continue
        cumulative += count
        # Right edge of this log bin (distance upper bound for the step).
        right_log = hist.log_min + (idx + 1) * log_span / hist.num_bins
        xs.append(10.0 ** right_log)
        ys.append(cumulative / hist.total)

    if hist.max_distance > 0 and xs[-1] < hist.max_distance:
        xs.append(float(hist.max_distance))
        ys.append(1.0)
    elif ys[-1] < 1.0:
        xs.append(xs[-1])
        ys.append(1.0)

    return xs, ys


def distance_at_fraction_from_cdf(
    xs: list[float], ys: list[float], fraction: float,
) -> float | None:
    """Return the smallest distance where the empirical CDF reaches `fraction`."""
    for x, y in zip(xs, ys):
        if y >= fraction:
            return x
    return None


def max_distance_at_fraction_across_curves(
    curves: list[tuple[str, list[float], list[float], str]],
    fraction: float,
) -> float | None:
    distances: list[float] = []
    for _label, xs, ys, _color in curves:
        dist = distance_at_fraction_from_cdf(xs, ys, fraction)
        if dist is not None:
            distances.append(dist)
    return max(distances) if distances else None


def percentile_from_histogram(hist: DistanceHistogram, fraction: float) -> float:
    if hist.total <= 0:
        return 0.0
    target = fraction * hist.total
    cumulative = 0.0
    log_span = hist.log_max - hist.log_min
    for idx, count in enumerate(hist.counts):
        cumulative += count
        if cumulative >= target:
            right_log = hist.log_min + (idx + 1) * log_span / hist.num_bins
            return 10.0 ** right_log
    return float(hist.max_distance)


def write_summary_csv(
    output_dir: Path,
    aggregate: DistanceHistogram,
    per_workload: dict[str, DistanceHistogram],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / SUMMARY_CSV_NAME
    with summary_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "scope",
                "pair_count",
                "max_distance",
                "p50_distance",
                "p90_distance",
                "p99_distance",
            ]
        )
        scopes: list[tuple[str, DistanceHistogram]] = []
        for workload in ordered_workloads(per_workload):
            scopes.append((rename_workload(workload), per_workload[workload]))
        scopes.append((AGGREGATE_LABEL, aggregate))
        for label, hist in scopes:
            writer.writerow(
                [
                    label,
                    hist.total,
                    hist.max_distance,
                    int(percentile_from_histogram(hist, 0.50)),
                    int(percentile_from_histogram(hist, 0.90)),
                    int(percentile_from_histogram(hist, 0.99)),
                ]
            )
    return summary_path


def write_cdf_csv(
    output_dir: Path,
    label: str,
    xs: list[float],
    ys: list[float],
) -> Path:
    slug = (
        label.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )
    path = output_dir / f"{CDF_CSV_PREFIX}-{slug}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["micro_op_distance", "cdf_fraction"])
        for x, y in zip(xs, ys):
            writer.writerow([f"{x:.6f}", f"{y:.6f}"])
    return path


def cdf_csv_slug(workload: str) -> str:
    return (
        workload.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


def resolve_cdf_csv(output_dir: Path, workload: str) -> Path | None:
    slug = cdf_csv_slug(workload)
    candidates = [
        output_dir / f"{CDF_CSV_PREFIX}-{slug}.csv",
        output_dir / f"{LEGACY_CDF_CSV_PREFIX}_{slug}.csv",
    ]
    for alias in WORKLOAD_DIR_ALIASES.get(workload, ()):
        alias_slug = cdf_csv_slug(alias)
        candidates.append(output_dir / f"{CDF_CSV_PREFIX}-{alias_slug}.csv")
        candidates.append(output_dir / f"{LEGACY_CDF_CSV_PREFIX}_{alias_slug}.csv")
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_cdf_csv(path: Path) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            xs.append(float(row["micro_op_distance"]))
            ys.append(float(row["cdf_fraction"]))
    if not xs:
        raise SystemExit(f"Empty CDF CSV: {path}")
    return xs, ys


def load_cached_curves(
    output_dir: Path,
    workloads: list[str],
) -> list[tuple[str, list[float], list[float], str]]:
    curves: list[tuple[str, list[float], list[float], str]] = []
    missing: list[str] = []
    for workload in workloads:
        path = resolve_cdf_csv(output_dir, workload)
        if path is None:
            missing.append(
                str(output_dir / f"{CDF_CSV_PREFIX}-{cdf_csv_slug(workload)}.csv")
            )
            continue
        xs, ys = load_cdf_csv(path)
        curves.append(
            (
                workload_display_name(workload),
                xs,
                ys,
                workload_color(workload),
            )
        )
    if missing:
        raise SystemExit(
            "Missing cached CDF CSVs (run without --replot first):\n  "
            + "\n  ".join(missing)
        )
    if not curves:
        raise SystemExit(f"No cached CDF curves found under {output_dir}")
    return curves


def print_scope_summary(label: str, hist: DistanceHistogram) -> None:
    if hist.total <= 0:
        print(f"{label}: no pairs", flush=True)
        return
    p50 = percentile_from_histogram(hist, 0.50)
    p90 = percentile_from_histogram(hist, 0.90)
    p99 = percentile_from_histogram(hist, 0.99)
    print(
        f"{label}: pairs={hist.total:,}  "
        f"max={hist.max_distance:,}  "
        f"p50={int(p50):,}  p90={int(p90):,}  p99={int(p99):,}",
        flush=True,
    )


def _ensure_noto_serif() -> None:
    """Register user-local Noto Serif TTFs so matplotlib can render FONT_FAMILY."""
    global _noto_serif_registered
    if _noto_serif_registered:
        return
    import matplotlib.font_manager as fm

    for name in ("NotoSerif-Regular.ttf", "NotoSerif-Bold.ttf"):
        font_path = NOTO_SERIF_FONT_DIR / name
        if font_path.is_file():
            fm.fontManager.addfont(str(font_path))
    _noto_serif_registered = True


def _apply_plot_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "font.size": PLOT_TICK_FONT,
            "axes.labelsize": PLOT_LABEL_FONT,
            "xtick.labelsize": PLOT_TICK_FONT,
            "ytick.labelsize": PLOT_TICK_FONT,
            "legend.fontsize": PLOT_LEGEND_FONT,
        }
    )


def _style_axes(ax) -> None:
    ax.grid(
        True,
        axis="y",
        linestyle=":",
        color="black",
        alpha=0.65,
        linewidth=1.0,
        zorder=0,
    )
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")


def _save_figure(fig, output_path: Path) -> None:
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf", "eps"):
        fig.savefig(f"{output_path}.{ext}", bbox_inches="tight", dpi=300)


def workload_color(workload: str) -> str:
    return WORKLOAD_COLORS.get(workload, "#808080")


def ordered_workloads(per_workload: dict[str, DistanceHistogram]) -> list[str]:
    ordered = [wl for wl in DEFAULT_WORKLOADS if wl in per_workload]
    extras = sorted(set(per_workload.keys()) - set(ordered))
    return ordered + extras


def _legend_handles(
    curves: list[tuple[str, list[float], list[float], str]],
) -> list:
    from matplotlib.patches import Patch

    return [
        Patch(
            facecolor=color,
            edgecolor="black",
            linewidth=LEGEND_EDGE_WIDTH,
            label=label,
        )
        for label, _xs, _ys, color in curves
    ]


def plot_cdf(
    curves: list[tuple[str, list[float], list[float], str]],
    output_dir: Path,
) -> None:
    import matplotlib.pyplot as plt

    _ensure_noto_serif()
    _apply_plot_style()

    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
    xmax = 1.0
    for _label, xs, ys, _color in curves:
        if not xs:
            continue
        # Trim the long flat 100% tail so the useful rise uses the width.
        cutoff = next(
            (x for x, y in zip(xs, ys) if y >= 0.995),
            xs[-1],
        )
        xmax = max(xmax, cutoff)

    xmin = 1.0
    for label, xs, ys, color in curves:
        ax.plot(xs, ys, label=label, color=color, linewidth=2.2)

    log_x_min = math.log10(xmin)
    log_x_max = math.log10(xmax)
    for fraction, ref_label, label_offset in CDF_REFERENCE_LEVELS:
        all_apps_dist = max_distance_at_fraction_across_curves(curves, fraction)
        if all_apps_dist is not None:
            print(
                f"All-apps {ref_label} distance (max across workloads): "
                f"{int(all_apps_dist):,} micro-ops",
                flush=True,
            )

        ax.axhline(
            fraction,
            color=CDF_REFERENCE_COLOR,
            linestyle="--",
            linewidth=1.4,
            zorder=1,
        )
        if all_apps_dist is not None:
            ax.axvline(
                all_apps_dist,
                color=CDF_REFERENCE_COLOR,
                linestyle="--",
                linewidth=1.4,
                zorder=1,
            )
            label_xy = (all_apps_dist, fraction)
        else:
            label_xy = (
                10.0 ** (log_x_min + 0.5 * (log_x_max - log_x_min)),
                fraction,
            )

        ax.annotate(
            ref_label,
            xy=label_xy,
            xytext=label_offset,
            textcoords="offset points",
            ha="left",
            va="top",
            fontsize=CDF_REFERENCE_LABEL_FONT,
            color=CDF_REFERENCE_COLOR,
            fontfamily=FONT_FAMILY,
        )

    ax.set_xscale("log")
    ax.set_xlim(xmin, xmax * 1.15)
    ax.set_ylim(0.0, 1.0)
    ax.set_yticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0", "20", "40", "60", "80", "100"])
    ax.set_xlabel(
        "Distance in micro-ops between fusible load pairs (log scale)",
        fontsize=PLOT_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.set_ylabel("CDF", fontsize=PLOT_LABEL_FONT, fontfamily=FONT_FAMILY)
    ax.tick_params(axis="both", labelsize=PLOT_TICK_FONT)
    for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
        tick_label.set_fontfamily(FONT_FAMILY)
    _style_axes(ax)

    fig.subplots_adjust(left=0.07, bottom=0.18, right=0.985, top=0.97)
    if len(curves) > 1:
        legend = ax.legend(
            handles=_legend_handles(curves),
            loc="lower right",
            bbox_to_anchor=(0.99, 0.03),
            ncol=2,
            frameon=True,
            fancybox=False,
            edgecolor=LEGEND_FRAME_COLOR,
            fontsize=PLOT_LEGEND_FONT,
            prop={"family": FONT_FAMILY, "size": PLOT_LEGEND_FONT},
            handlelength=1.05,
            handleheight=1.05,
            borderpad=0.3,
            labelspacing=0.22,
            handletextpad=0.4,
            columnspacing=0.75,
        )
        frame = legend.get_frame()
        frame.set_edgecolor(LEGEND_FRAME_COLOR)
        frame.set_linewidth(LEGEND_FRAME_WIDTH)
        frame.set_facecolor("white")
        frame.set_alpha(1.0)

    stem = output_dir / FIGURE_STEM
    _save_figure(fig, stem)
    plt.close(fig)
    print(f"Wrote figure: {stem}.{{png,pdf,eps}}", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    workloads_arg = None if args.all_workloads else args.workloads

    if args.replot:
        workloads = workloads_arg if workloads_arg is not None else DEFAULT_WORKLOADS
        # Preserve DEFAULT_WORKLOADS order when using the paper subset.
        if workloads_arg is not None:
            ordered = [wl for wl in DEFAULT_WORKLOADS if wl in workloads]
            ordered += [wl for wl in workloads if wl not in set(ordered)]
            workloads = ordered
        curves = load_cached_curves(output_dir, workloads)
        print(f"Replotting {len(curves)} cached CDF curves from {output_dir}", flush=True)
        plot_cdf(curves, output_dir)
        return

    candidates_dir = args.candidates_dir.expanduser().resolve()
    if not candidates_dir.is_dir():
        raise SystemExit(f"Candidates directory does not exist: {candidates_dir}")
    if args.num_bins < 10:
        raise SystemExit("--num-bins must be at least 10")

    tasks = discover_candidate_files(candidates_dir, workloads_arg)
    aggregate, per_workload = stream_histograms(
        tasks,
        log_max=args.log_max,
        num_bins=args.num_bins,
    )
    summary_path = write_summary_csv(output_dir, aggregate, per_workload)
    print(f"Wrote summary: {summary_path}", flush=True)

    curves: list[tuple[str, list[float], list[float], str]] = []

    workloads = ordered_workloads(per_workload)
    for workload in workloads:
        hist = per_workload[workload]
        xs, ys = histogram_to_cdf(hist)
        label = workload_display_name(workload)
        write_cdf_csv(output_dir, workload, xs, ys)
        print_scope_summary(label, hist)
        curves.append((label, xs, ys, workload_color(workload)))

    if args.aggregate:
        xs, ys = histogram_to_cdf(aggregate)
        write_cdf_csv(output_dir, "all_workloads", xs, ys)
        print_scope_summary(AGGREGATE_LABEL, aggregate)
        curves.append((AGGREGATE_LABEL, xs, ys, AGGREGATE_COLOR))

    if not curves:
        raise SystemExit("No CDF curves to plot.")

    plot_cdf(curves, output_dir)


if __name__ == "__main__":
    main()
