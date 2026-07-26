#!/usr/bin/env python3
"""CDF of dominant cache-block offset-delta fraction per static fusible load pair.

Pass-1 ideal-fusion candidate CSVs list one row per *dynamic* fusible load pair:
  load1_pc, ..., load1_block_offset, ..., load2_pc, ..., load2_block_offset, ...

For each static pair (load1_pc, load2_pc) within a workload, we compute:
  offset_delta = load2_block_offset - load1_block_offset
  dominant_fraction = count(mode offset_delta) / total dynamic instances

The CDF plots, for fraction x on the x-axis, the fraction of static pairs whose
dominant_fraction is <= x.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_offset_delta_cdf.py \
  --candidates-dir /dev/shm/baseline/ideal_fusion_candidates \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/offset_delta_cdf
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    DEFAULT_SCARAB_ROOT,
    DEFAULT_RESULTS_ROOT,
    FONT_FAMILY,
    IDEAL_FUSION_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    rename_workload,
)

DEFAULT_CANDIDATES_DIR = Path("/dev/shm/baseline/ideal_fusion_candidates")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "offset_delta_cdf"

COL_LOAD1_PC = 0
COL_LOAD1_BLOCK_OFFSET = 2
COL_LOAD2_PC = 5
COL_LOAD2_BLOCK_OFFSET = 7

AGGREGATE_LABEL = "All workloads"
AGGREGATE_COLOR = IDEAL_FUSION_COLOR


@dataclass(frozen=True)
class PairStats:
    workload: str
    load1_pc: str
    load2_pc: str
    dynamic_count: int
    unique_deltas: int
    dominant_delta: int
    dominant_fraction: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot CDF of dominant offset-delta fraction per static fusible pair."
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
        "--weight-by-dynamic-count",
        action="store_true",
        help="Weight each static pair by its dynamic instance count in the CDF.",
    )
    parser.add_argument(
        "--per-workload",
        action="store_true",
        help="Draw one CDF curve per workload in addition to the aggregate curve.",
    )
    parser.add_argument(
        "--no-aggregate",
        action="store_true",
        help="Omit the aggregate curve (use with --per-workload).",
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


def accumulate_pairs(
    tasks: list[tuple[str, str, Path]],
) -> dict[tuple[str, str, str], Counter[int]]:
    """Map (workload, load1_pc, load2_pc) -> Counter[offset_delta]."""
    groups: dict[tuple[str, str, str], Counter[int]] = defaultdict(Counter)
    rows_read = 0

    for workload, _cluster_id, csv_path in tasks:
        with csv_path.open() as fh:
            fh.readline()  # header
            for line in fh:
                parts = line.split(",")
                if len(parts) <= COL_LOAD2_BLOCK_OFFSET:
                    continue
                rows_read += 1
                try:
                    offset_delta = int(parts[COL_LOAD2_BLOCK_OFFSET]) - int(
                        parts[COL_LOAD1_BLOCK_OFFSET]
                    )
                except ValueError:
                    continue
                key = (workload, parts[COL_LOAD1_PC], parts[COL_LOAD2_PC])
                groups[key][offset_delta] += 1

    print(f"Read {rows_read:,} dynamic candidate rows", flush=True)
    print(f"Found {len(groups):,} static (workload, LD1PC, LD2PC) groups", flush=True)
    return groups


def pair_stats_from_groups(
    groups: dict[tuple[str, str, str], Counter[int]],
) -> list[PairStats]:
    stats: list[PairStats] = []
    for (workload, load1_pc, load2_pc), counter in groups.items():
        total = sum(counter.values())
        if total <= 0:
            continue
        dominant_delta, dominant_count = counter.most_common(1)[0]
        stats.append(
            PairStats(
                workload=workload,
                load1_pc=load1_pc,
                load2_pc=load2_pc,
                dynamic_count=total,
                unique_deltas=len(counter),
                dominant_delta=dominant_delta,
                dominant_fraction=dominant_count / total,
            )
        )
    return stats


def dominant_fractions_for_scope(
    stats: list[PairStats],
    workload: str | None,
) -> list[float]:
    if workload is None:
        scoped = stats
    else:
        scoped = [row for row in stats if row.workload == workload]
    return [row.dominant_fraction for row in scoped]


def weights_for_scope(
    stats: list[PairStats],
    workload: str | None,
) -> list[float]:
    if workload is None:
        scoped = stats
    else:
        scoped = [row for row in stats if row.workload == workload]
    return [float(row.dynamic_count) for row in scoped]


def build_cdf(
    values: list[float],
    weights: list[float] | None = None,
) -> tuple[list[float], list[float]]:
    if not values:
        return [], []
    if weights is not None and len(weights) != len(values):
        raise ValueError("values and weights must have the same length")

    pairs = sorted(zip(values, weights or [1.0] * len(values)), key=lambda item: item[0])
    total_weight = sum(weight for _, weight in pairs)
    if total_weight <= 0:
        return [], []

    xs: list[float] = [0.0]
    ys: list[float] = [0.0]
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        xs.append(value)
        ys.append(cumulative / total_weight)
    xs.append(1.0)
    ys.append(1.0)
    return xs, ys


def write_summary_csv(output_dir: Path, stats: list[PairStats]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "offset_delta_pair_summary.csv"
    with summary_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "load1_pc",
                "load2_pc",
                "dynamic_count",
                "unique_deltas",
                "dominant_delta",
                "dominant_fraction",
            ]
        )
        for row in sorted(
            stats,
            key=lambda item: (
                item.workload,
                -item.dominant_fraction,
                -item.dynamic_count,
                item.load1_pc,
                item.load2_pc,
            ),
        ):
            writer.writerow(
                [
                    row.workload,
                    row.load1_pc,
                    row.load2_pc,
                    row.dynamic_count,
                    row.unique_deltas,
                    row.dominant_delta,
                    f"{row.dominant_fraction:.6f}",
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
    path = output_dir / f"offset_delta_cdf_{slug}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["dominant_fraction", "cdf_fraction"])
        for x, y in zip(xs, ys):
            writer.writerow([f"{x:.6f}", f"{y:.6f}"])
    return path


def print_scope_summary(label: str, stats: list[PairStats]) -> None:
    if not stats:
        print(f"{label}: no static pairs", flush=True)
        return
    fractions = [row.dominant_fraction for row in stats]
    ge90 = sum(1 for value in fractions if value >= 0.9) / len(fractions)
    eq1 = sum(1 for value in fractions if value >= 1.0 - 1e-12) / len(fractions)
    one_delta = sum(1 for row in stats if row.unique_deltas == 1) / len(stats)
    print(
        f"{label}: pairs={len(stats):,}  "
        f"median_dominant_frac={sorted(fractions)[len(fractions) // 2]:.3f}  "
        f">=90%={ge90:.1%}  exactly_one_delta={one_delta:.1%}  perfect(1.0)={eq1:.1%}",
        flush=True,
    )


def plot_cdf(
    curves: list[tuple[str, list[float], list[float], str]],
    output_dir: Path,
    *,
    weight_by_dynamic_count: bool,
) -> None:
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
            "legend.fontsize": IPC_LEGEND_FONT,
        }
    )

    fig, ax = plt.subplots(figsize=(10, 7))
    for label, xs, ys, color in curves:
        ax.plot(xs, ys, label=label, color=color, linewidth=2.5)

    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_xlabel("Dominant offset-delta fraction")
    ax.set_ylabel("Fraction of static fusible pairs")
    ax.grid(True, linestyle=":", alpha=0.4)
    if len(curves) > 1:
        ax.legend(loc="lower right", frameon=True)
    title_suffix = "dynamic-weighted" if weight_by_dynamic_count else "unweighted"
    ax.set_title(f"Offset-delta predictability ({title_suffix})")

    stem = output_dir / "offset_delta_cdf"
    for ext in ("png", "pdf", "eps"):
        fig.savefig(f"{stem}.{ext}", bbox_inches="tight", dpi=300)
    plt.close(fig)
    print(f"Wrote figure: {stem}.{{png,pdf,eps}}", flush=True)


def workload_color(workload: str, index: int) -> str:
    palette = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    ]
    return palette[index % len(palette)]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    candidates_dir = args.candidates_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not candidates_dir.is_dir():
        raise SystemExit(f"Candidates directory does not exist: {candidates_dir}")

    tasks = discover_candidate_files(candidates_dir, args.workloads)
    groups = accumulate_pairs(tasks)
    stats = pair_stats_from_groups(groups)
    summary_path = write_summary_csv(output_dir, stats)
    print(f"Wrote pair summary: {summary_path}", flush=True)

    workloads = sorted({row.workload for row in stats})
    curves: list[tuple[str, list[float], list[float], str]] = []

    if not args.no_aggregate:
        values = dominant_fractions_for_scope(stats, workload=None)
        weights = (
            weights_for_scope(stats, workload=None)
            if args.weight_by_dynamic_count
            else None
        )
        xs, ys = build_cdf(values, weights)
        write_cdf_csv(output_dir, "all_workloads", xs, ys)
        print_scope_summary(AGGREGATE_LABEL, stats)
        curves.append((AGGREGATE_LABEL, xs, ys, AGGREGATE_COLOR))

    if args.per_workload:
        for index, workload in enumerate(workloads):
            scoped = [row for row in stats if row.workload == workload]
            values = dominant_fractions_for_scope(stats, workload)
            weights = (
                weights_for_scope(stats, workload)
                if args.weight_by_dynamic_count
                else None
            )
            xs, ys = build_cdf(values, weights)
            label = rename_workload(workload)
            write_cdf_csv(output_dir, workload, xs, ys)
            print_scope_summary(label, scoped)
            curves.append((label, xs, ys, workload_color(workload, index)))

    if not curves:
        raise SystemExit("No CDF curves to plot (use aggregate and/or --per-workload).")

    plot_cdf(curves, output_dir, weight_by_dynamic_count=args.weight_by_dynamic_count)


if __name__ == "__main__":
    main()
