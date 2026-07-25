#!/usr/bin/env python3
"""CDF of micro-op distance between dynamic fusible load pairs (ideal fusion pass 1).

Pass-1 candidate CSVs list one row per dynamic fusible LD1/LD2 pair. The last
column is micro_op_distance = load2_micro_op_num - load1_micro_op_num.

This script plots an empirical CDF with:
  x-axis: micro-op distance (log scale)
  y-axis: fraction of pairs whose distance is <= x

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_micro_op_distance_cdf.py \
  --candidates-dir /dev/shm/baseline/ideal_fusion_candidates \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/micro_op_distance_cdf
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
    SIMPOINT_WORKLOADS,
    rename_workload,
)

DEFAULT_CANDIDATES_DIR = Path("/dev/shm/baseline/ideal_fusion_candidates")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "micro_op_distance_cdf"

COL_MICRO_OP_DISTANCE = 10
AGGREGATE_LABEL = "All workloads"
AGGREGATE_COLOR = IDEAL_FUSION_COLOR
DEFAULT_NUM_LOG_BINS = 800
DEFAULT_LOG_MAX = 8.0  # log10 ceiling before streaming (10^8 micro-ops)

# Styling aligned with plot_fusion_predictability.py CDF figures.
PLOT_LABEL_FONT = 22
PLOT_TICK_FONT = 20
PLOT_LEGEND_FONT = 14
NOTO_SERIF_FONT_DIR = Path.home() / ".local/share/fonts" / "noto-serif"
_noto_serif_registered = False

# Per-app colors (user palette + distinct 9th for ClickHouse).
WORKLOAD_COLORS: dict[str, str] = {
    "bfs": "#017E7C",
    "dfs": "#8F993E",
    "pagerank": "#016895",
    "appworld": "#E98300",
    "corebench": "#C74632",
    "terminal_bench": "#620059",
    "duckdb": "#9475BD",
    "rocksdb": "#FEC51D",
    "clickhouse": "#795548",
}
LEGEND_EDGE_WIDTH = 1.2
LEGEND_FRAME_WIDTH = 1.0
LEGEND_FRAME_COLOR = "#000000"
PLOT_FIGSIZE = (10.0, 3.5)
# (CDF fraction, label, label offset in points from intersection)
CDF_REFERENCE_LEVELS: tuple[tuple[float, str, tuple[int, int]], ...] = (
    (0.80, "p80", (5, -10)),
    (0.95, "p95", (5, -10)),
)
CDF_REFERENCE_COLOR = "#C74632"
CDF_REFERENCE_LABEL_FONT = 12


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
        default=None,
        help="Optional workload subset (default: every directory under candidates-dir).",
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
    return parser.parse_args(argv)


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
        wl_dir = candidates_dir / workload
        if not wl_dir.is_dir():
            raise SystemExit(f"Missing workload directory: {wl_dir}")
        for csv_path in sorted(wl_dir.glob("*.csv")):
            tasks.append((workload, csv_path.stem, csv_path))
    if not tasks:
        raise SystemExit(f"No candidate CSVs found under {candidates_dir}")
    return tasks


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
    summary_path = output_dir / "micro_op_distance_summary.csv"
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
    path = output_dir / f"micro_op_distance_cdf_{slug}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["micro_op_distance", "cdf_fraction"])
        for x, y in zip(xs, ys):
            writer.writerow([f"{x:.6f}", f"{y:.6f}"])
    return path


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
            "axes.labelsize": PLOT_LABEL_FONT,
            "xtick.labelsize": PLOT_TICK_FONT,
            "ytick.labelsize": PLOT_TICK_FONT,
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
    fig.subplots_adjust(left=0.14, bottom=0.14, right=0.97, top=0.97)
    for ext in ("png", "pdf", "eps"):
        fig.savefig(f"{output_path}.{ext}", bbox_inches="tight", dpi=300)


def workload_color(workload: str) -> str:
    return WORKLOAD_COLORS.get(workload, "#808080")


def ordered_workloads(per_workload: dict[str, DistanceHistogram]) -> list[str]:
    ordered = [wl for wl in SIMPOINT_WORKLOADS if wl in per_workload]
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
    for _label, xs, _ys, _color in curves:
        if xs:
            xmax = max(xmax, max(xs))

    xmin = 1.0
    for label, xs, ys, color in curves:
        ax.plot(xs, ys, label=label, color=color, linewidth=2.5)

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
            linewidth=1.8,
            zorder=1,
        )
        if all_apps_dist is not None:
            ax.axvline(
                all_apps_dist,
                color=CDF_REFERENCE_COLOR,
                linestyle="--",
                linewidth=1.8,
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
    ax.set_xlim(xmin, xmax * 1.05)
    ax.set_ylim(0.0, 1.0)
    ax.set_yticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0", "20", "40", "60", "80", "100"])
    ax.set_xlabel(
        "Distance in micro-ops between fusible load pairs (log scale)",
        fontsize=PLOT_LABEL_FONT,
    )
    ax.set_ylabel("CDF", fontsize=PLOT_LABEL_FONT)
    _style_axes(ax)
    if len(curves) > 1:
        legend = ax.legend(
            handles=_legend_handles(curves),
            loc="lower right",
            bbox_to_anchor=(0.99, 0.02),
            ncol=2,
            frameon=True,
            fancybox=False,
            edgecolor=LEGEND_FRAME_COLOR,
            fontsize=PLOT_LEGEND_FONT,
            handlelength=1.0,
            handleheight=1.0,
            borderpad=0.35,
            labelspacing=0.35,
            handletextpad=0.45,
        )
        frame = legend.get_frame()
        frame.set_edgecolor(LEGEND_FRAME_COLOR)
        frame.set_linewidth(LEGEND_FRAME_WIDTH)
        frame.set_facecolor("white")
        frame.set_alpha(1.0)

    stem = output_dir / "micro_op_distance_cdf"
    _save_figure(fig, stem)
    plt.close(fig)
    print(f"Wrote figure: {stem}.{{png,pdf,eps}}", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    candidates_dir = args.candidates_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not candidates_dir.is_dir():
        raise SystemExit(f"Candidates directory does not exist: {candidates_dir}")
    if args.num_bins < 10:
        raise SystemExit("--num-bins must be at least 10")

    tasks = discover_candidate_files(candidates_dir, args.workloads)
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
        label = rename_workload(workload)
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
