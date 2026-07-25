#!/usr/bin/env python3
"""SimPoint-weighted predictability of ideal load-fusion spatial relationships.

This is an *ideal* fusion characterization: it does not evaluate I-Fuse's
runtime predictor. It asks, given the ideal-fusion-candidate dumps (every
dynamic fusible LD1/LD2 pair Scarab found in pass-1), how predictable each
static (LD1 PC, LD2 PC) pair's behavior is:

  1. Offset-delta predictability: cache_block_offset(LD2) - cache_block_offset(LD1)
  2. LD2 access-size predictability

For each static pair we assume a predictor that always guesses the majority
(most frequent) value and report that predictor's accuracy.

SimPoint weighting
-------------------
A candidate dump is one CSV per (workload, cluster_id) simpoint. Simpoints are
fixed-length trace segments, so raw dynamic counts do not reflect how much of
real execution a segment represents -- that is what the SimPoint weight (from
opt.p/opt.w, the same weights used for our IPC evaluations) is for. For each
pair we first normalize within each simpoint (count / simpoint total), then
weight-average those per-simpoint distributions by the simpoint's weight. The
majority value and its accuracy are read off the resulting weighted
distribution. This is the same weight-average-of-per-simpoint-values pattern
used elsewhere in this repo (see plot_load_latency.py).

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_fusion_predictability.py \
  --candidates-dir /dev/shm/baseline/ideal_fusion_candidates \
  --trace-root /dev/shm/baseline/simpoint_traces \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/fusion_predictability
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    DEFAULT_RESULTS_ROOT,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    IDEAL_FUSION_COLOR,
    load_simpoint_trace_weights,
    rename_workload,
)

DEFAULT_CANDIDATES_DIR = Path("/dev/shm/baseline/ideal_fusion_candidates")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "fusion_predictability"

CANDIDATE_WORKLOADS = [
    "appworld",
    "bfs",
    "clickhouse",
    "core_bench",
    "dfs",
    "duckdb",
    "pagerank",
    "rocksdb",
    "terminal_bench",
]

# Candidate CSV columns we need (see header: load1_pc, load1_data_addr,
# load1_block_offset, load1_mem_size, load1_micro_op_num, load2_pc,
# load2_data_addr, load2_block_offset, load2_mem_size, load2_micro_op_num,
# micro_op_distance).
CSV_COLUMNS = [0, 2, 5, 7, 8]
CSV_NAMES = [
    "load1_pc",
    "load1_block_offset",
    "load2_pc",
    "load2_block_offset",
    "load2_mem_size",
]

HIGHLY_PREDICTABLE_THRESHOLD = 0.95
PREDICTABLE_THRESHOLD = 0.80


@dataclass(frozen=True)
class PairAccuracy:
    """SimPoint-weighted predictability summary for one static (LD1 PC, LD2 PC) pair."""

    workload: str
    load1_pc: str
    load2_pc: str
    raw_observations: int
    raw_unique_deltas: int
    delta_accuracy: float
    dominant_delta: int
    size_accuracy: float
    dominant_size: int
    dynamic_weight: float  # SimPoint-weighted dynamic-instance mass, for dynamic-weighted reporting


def classify(accuracy: float) -> str:
    """Bucket a majority-value prediction accuracy into the paper's three bands."""
    if accuracy >= HIGHLY_PREDICTABLE_THRESHOLD:
        return "highly_predictable"
    if accuracy >= PREDICTABLE_THRESHOLD:
        return "predictable"
    return "unpredictable"


def discover_simpoint_csvs(candidates_dir: Path, workload: str) -> list[tuple[str, Path]]:
    """Return [(cluster_id, csv_path), ...] for a workload's candidate dumps."""
    wl_dir = candidates_dir / workload
    if not wl_dir.is_dir():
        raise SystemExit(f"Missing candidates directory: {wl_dir}")
    return sorted((p.stem, p) for p in wl_dir.glob("*.csv"))


def load_simpoint_frame(csv_path: Path) -> pd.DataFrame:
    """Read one simpoint's candidate dump and compute the cache-block offset delta."""
    df = pd.read_csv(csv_path, header=0, usecols=CSV_COLUMNS, names=CSV_NAMES)
    df["offset_delta"] = df["load2_block_offset"] - df["load1_block_offset"]
    return df


def pair_distributions(df: pd.DataFrame) -> tuple[dict, pd.Series, pd.Series]:
    """Per-pair dynamic totals, and per-(pair, value) counts for delta and LD2 size."""
    totals = df.groupby(["load1_pc", "load2_pc"]).size().to_dict()
    delta_counts = df.groupby(["load1_pc", "load2_pc", "offset_delta"]).size()
    size_counts = df.groupby(["load1_pc", "load2_pc", "load2_mem_size"]).size()
    return totals, delta_counts, size_counts


class WorkloadAccumulator:
    """Combines per-simpoint pair distributions into SimPoint-weighted pair statistics.

    Every value is first normalized within its own simpoint (count / simpoint
    total) before being scaled by that simpoint's weight, so a simpoint that
    happens to contain more dynamic instances does not out-vote a
    higher-weight simpoint with fewer.
    """

    def __init__(self) -> None:
        self.raw_delta: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.raw_size: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.weighted_delta_mass: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.weighted_size_mass: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.weight_seen: dict[tuple[str, str], float] = defaultdict(float)
        self.dynamic_weight: dict[tuple[str, str], float] = defaultdict(float)

    def add_simpoint(
        self,
        weight: float,
        totals: dict,
        delta_counts: pd.Series,
        size_counts: pd.Series,
    ) -> None:
        for pair, total in totals.items():
            self.weight_seen[pair] += weight
            self.dynamic_weight[pair] += weight * total

        for (load1_pc, load2_pc, delta), count in delta_counts.items():
            pair = (load1_pc, load2_pc)
            self.raw_delta[pair][delta] += count
            self.weighted_delta_mass[pair][delta] += weight * count / totals[pair]

        for (load1_pc, load2_pc, size), count in size_counts.items():
            pair = (load1_pc, load2_pc)
            self.raw_size[pair][size] += count
            self.weighted_size_mass[pair][size] += weight * count / totals[pair]

    def finalize(self, workload: str) -> list[PairAccuracy]:
        records = []
        for pair, weight_sum in self.weight_seen.items():
            if weight_sum <= 0:
                continue
            load1_pc, load2_pc = pair
            dominant_delta, delta_mass = self.weighted_delta_mass[pair].most_common(1)[0]
            dominant_size, size_mass = self.weighted_size_mass[pair].most_common(1)[0]
            records.append(
                PairAccuracy(
                    workload=workload,
                    load1_pc=load1_pc,
                    load2_pc=load2_pc,
                    raw_observations=sum(self.raw_delta[pair].values()),
                    raw_unique_deltas=len(self.raw_delta[pair]),
                    delta_accuracy=delta_mass / weight_sum,
                    dominant_delta=dominant_delta,
                    size_accuracy=size_mass / weight_sum,
                    dominant_size=dominant_size,
                    dynamic_weight=self.dynamic_weight[pair],
                )
            )
        return records


def compute_workload_pairs(
    workload: str,
    candidates_dir: Path,
    sp_weights: dict[tuple[str, str], float],
) -> list[PairAccuracy]:
    """Parse every simpoint dump for a workload and return SimPoint-weighted pair stats."""
    accumulator = WorkloadAccumulator()
    for cluster_id, csv_path in discover_simpoint_csvs(candidates_dir, workload):
        weight = sp_weights.get((workload, cluster_id))
        if weight is None or weight <= 0:
            print(f"  skip {workload}/{cluster_id}: no SimPoint weight", flush=True)
            continue
        df = load_simpoint_frame(csv_path)
        totals, delta_counts, size_counts = pair_distributions(df)
        accumulator.add_simpoint(weight, totals, delta_counts, size_counts)
    return accumulator.finalize(workload)


# --------------------------------------------------------------------------
# Classification reporting
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkloadReport:
    workload: str
    num_pairs: int
    delta_highly_predictable_frac: float
    delta_predictable_or_better_frac: float
    size_highly_predictable_frac: float
    size_predictable_or_better_frac: float
    dynamic_delta_predictable_or_better_frac: float
    dynamic_size_predictable_or_better_frac: float


def fraction_at_least(values: list[float], threshold: float) -> float:
    if not values:
        return float("nan")
    return sum(1 for v in values if v >= threshold) / len(values)


def dynamic_weighted_fraction_at_least(
    accuracies: list[float], weights: list[float], threshold: float
) -> float:
    total_weight = sum(weights)
    if total_weight <= 0:
        return float("nan")
    matched_weight = sum(w for acc, w in zip(accuracies, weights) if acc >= threshold)
    return matched_weight / total_weight


def summarize_workload(workload: str, pairs: list[PairAccuracy]) -> WorkloadReport:
    delta_acc = [p.delta_accuracy for p in pairs]
    size_acc = [p.size_accuracy for p in pairs]
    dyn_weights = [p.dynamic_weight for p in pairs]
    return WorkloadReport(
        workload=workload,
        num_pairs=len(pairs),
        delta_highly_predictable_frac=fraction_at_least(delta_acc, HIGHLY_PREDICTABLE_THRESHOLD),
        delta_predictable_or_better_frac=fraction_at_least(delta_acc, PREDICTABLE_THRESHOLD),
        size_highly_predictable_frac=fraction_at_least(size_acc, HIGHLY_PREDICTABLE_THRESHOLD),
        size_predictable_or_better_frac=fraction_at_least(size_acc, PREDICTABLE_THRESHOLD),
        dynamic_delta_predictable_or_better_frac=dynamic_weighted_fraction_at_least(
            delta_acc, dyn_weights, PREDICTABLE_THRESHOLD
        ),
        dynamic_size_predictable_or_better_frac=dynamic_weighted_fraction_at_least(
            size_acc, dyn_weights, PREDICTABLE_THRESHOLD
        ),
    )


def suite_average(reports: list[WorkloadReport]) -> WorkloadReport:
    """Arithmetic mean across applications (each application counted once)."""
    n = len(reports)
    return WorkloadReport(
        workload="Suite average",
        num_pairs=sum(r.num_pairs for r in reports),
        delta_highly_predictable_frac=sum(r.delta_highly_predictable_frac for r in reports) / n,
        delta_predictable_or_better_frac=sum(r.delta_predictable_or_better_frac for r in reports) / n,
        size_highly_predictable_frac=sum(r.size_highly_predictable_frac for r in reports) / n,
        size_predictable_or_better_frac=sum(r.size_predictable_or_better_frac for r in reports) / n,
        dynamic_delta_predictable_or_better_frac=sum(
            r.dynamic_delta_predictable_or_better_frac for r in reports
        )
        / n,
        dynamic_size_predictable_or_better_frac=sum(
            r.dynamic_size_predictable_or_better_frac for r in reports
        )
        / n,
    )


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------


def write_pair_summary_csv(output_dir: Path, pairs: list[PairAccuracy]) -> Path:
    path = output_dir / "fusion_predictability_pair_summary.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "load1_pc",
                "load2_pc",
                "raw_observations",
                "raw_unique_deltas",
                "delta_accuracy",
                "dominant_delta",
                "delta_class",
                "size_accuracy",
                "dominant_size",
                "size_class",
                "dynamic_weight",
            ]
        )
        for p in sorted(pairs, key=lambda p: (p.workload, -p.delta_accuracy)):
            writer.writerow(
                [
                    p.workload,
                    p.load1_pc,
                    p.load2_pc,
                    p.raw_observations,
                    p.raw_unique_deltas,
                    f"{p.delta_accuracy:.6f}",
                    p.dominant_delta,
                    classify(p.delta_accuracy),
                    f"{p.size_accuracy:.6f}",
                    p.dominant_size,
                    classify(p.size_accuracy),
                    f"{p.dynamic_weight:.6f}",
                ]
            )
    return path


def write_workload_report_csv(output_dir: Path, reports: list[WorkloadReport]) -> Path:
    path = output_dir / "fusion_predictability_summary.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "display_name",
                "num_pairs",
                "delta_highly_predictable_frac",
                "delta_predictable_or_better_frac",
                "size_highly_predictable_frac",
                "size_predictable_or_better_frac",
                "dynamic_delta_predictable_or_better_frac",
                "dynamic_size_predictable_or_better_frac",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.workload,
                    rename_workload(r.workload) if r.workload != "Suite average" else r.workload,
                    r.num_pairs,
                    f"{r.delta_highly_predictable_frac:.4f}",
                    f"{r.delta_predictable_or_better_frac:.4f}",
                    f"{r.size_highly_predictable_frac:.4f}",
                    f"{r.size_predictable_or_better_frac:.4f}",
                    f"{r.dynamic_delta_predictable_or_better_frac:.4f}",
                    f"{r.dynamic_size_predictable_or_better_frac:.4f}",
                ]
            )
    return path


def write_computation_log(output_dir: Path, reports: list[WorkloadReport]) -> Path:
    path = output_dir / "fusion_predictability_computation_log.txt"
    with path.open("w") as fh:
        fh.write("Ideal fusion predictability (SimPoint-weighted)\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            f"Highly predictable: majority-value accuracy >= {HIGHLY_PREDICTABLE_THRESHOLD:.0%}\n"
            f"Predictable:        majority-value accuracy >= {PREDICTABLE_THRESHOLD:.0%}\n"
            f"Unpredictable:      majority-value accuracy <  {PREDICTABLE_THRESHOLD:.0%}\n\n"
        )
        for r in reports:
            label = "Suite average" if r.workload == "Suite average" else (
                f"{r.workload} ({rename_workload(r.workload)})"
            )
            fh.write(f"{label}\n")
            fh.write(f"  static PC pairs: {r.num_pairs}\n")
            fh.write(
                "  offset delta   -- highly predictable: "
                f"{r.delta_highly_predictable_frac:.1%}  "
                f"predictable-or-better: {r.delta_predictable_or_better_frac:.1%}\n"
            )
            fh.write(
                "  LD2 size       -- highly predictable: "
                f"{r.size_highly_predictable_frac:.1%}  "
                f"predictable-or-better: {r.size_predictable_or_better_frac:.1%}\n"
            )
            fh.write(
                "  dynamic-weighted, predictable-or-better -- "
                f"offset delta: {r.dynamic_delta_predictable_or_better_frac:.1%}  "
                f"LD2 size: {r.dynamic_size_predictable_or_better_frac:.1%}\n\n"
            )
    return path


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------


def build_cdf(values: list[float]) -> tuple[list[float], list[float]]:
    if not values:
        return [], []
    ordered = sorted(values)
    n = len(ordered)
    xs = [0.0] + ordered
    ys = [0.0] + [(i + 1) / n for i in range(n)]
    return xs, ys


def _style_axes(ax) -> None:
    ax.grid(True, linestyle=":", alpha=0.4)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")


PLOT_LABEL_FONT = 22
PLOT_TICK_FONT = 20


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


def _save_figure(fig, output_path: Path) -> None:
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0.14, bottom=0.14, right=0.97, top=0.97)
    for ext in ("png", "pdf", "eps"):
        fig.savefig(f"{output_path}.{ext}", bbox_inches="tight", dpi=300)


def plot_accuracy_cdf(
    pairs: list[PairAccuracy],
    accuracy_attr: str,
    xlabel: str,
    output_path: Path,
) -> None:
    import matplotlib.pyplot as plt

    _apply_plot_style()
    fig, ax = plt.subplots(figsize=(8, 6))
    xs, ys = build_cdf([getattr(p, accuracy_attr) for p in pairs])
    ax.plot(xs, ys, color=IDEAL_FUSION_COLOR, linewidth=2.5)
    ax.axvline(PREDICTABLE_THRESHOLD, color="gray", linestyle="--", linewidth=1.5, alpha=0.7)
    ax.axvline(HIGHLY_PREDICTABLE_THRESHOLD, color="gray", linestyle="--", linewidth=1.5, alpha=0.7)

    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_xlabel(xlabel, fontsize=PLOT_LABEL_FONT)
    ax.set_ylabel("Fraction of fusible load PC pairs", fontsize=PLOT_LABEL_FONT)
    _style_axes(ax)
    _save_figure(fig, output_path)
    plt.close(fig)


def plot_unique_delta_histogram(pairs: list[PairAccuracy], output_path: Path) -> None:
    import matplotlib.pyplot as plt

    _apply_plot_style()
    unique_delta_counts = [p.raw_unique_deltas for p in pairs]
    max_bucket = 6  # buckets: 1, 2, 3, 4, 5, ">=6"
    bucketed = [min(count, max_bucket) for count in unique_delta_counts]
    histogram = Counter(bucketed)
    labels = [str(i) for i in range(1, max_bucket)] + [f">={max_bucket}"]
    heights = [histogram.get(i, 0) for i in range(1, max_bucket + 1)]
    total = sum(heights)
    fractions = [h / total for h in heights]

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.bar(labels, fractions, color=IDEAL_FUSION_COLOR, edgecolor="black", linewidth=2.0, zorder=3)
    ax.set_xlabel("Unique offset deltas observed per PC pair", fontsize=PLOT_LABEL_FONT)
    ax.set_ylabel("Fraction of fusible load PC pairs", fontsize=PLOT_LABEL_FONT)
    _style_axes(ax)
    _save_figure(fig, output_path)
    plt.close(fig)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Characterize the predictability of ideal load-fusion candidates."
    )
    parser.add_argument("--candidates-dir", type=Path, default=DEFAULT_CANDIDATES_DIR)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--workloads",
        nargs="*",
        default=None,
        help="Optional workload subset (default: all candidate workloads).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    candidates_dir = args.candidates_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    workloads = args.workloads or CANDIDATE_WORKLOADS

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    all_pairs: list[PairAccuracy] = []
    reports: list[WorkloadReport] = []
    for workload in workloads:
        print(f"Processing {workload}...", flush=True)
        pairs = compute_workload_pairs(workload, candidates_dir, sp_weights)
        if not pairs:
            print(f"  skip {workload}: no candidate pairs found", flush=True)
            continue
        all_pairs.extend(pairs)
        report = summarize_workload(workload, pairs)
        reports.append(report)
        print(
            f"  {workload:14s}  pairs={report.num_pairs:6d}  "
            f"delta highly-predictable={report.delta_highly_predictable_frac:.1%}  "
            f"size highly-predictable={report.size_highly_predictable_frac:.1%}",
            flush=True,
        )

    if not reports:
        raise SystemExit("No workloads produced candidate pairs.")

    reports.append(suite_average(reports))
    output_dir.mkdir(parents=True, exist_ok=True)

    write_pair_summary_csv(output_dir, all_pairs)
    write_workload_report_csv(output_dir, reports)
    write_computation_log(output_dir, reports)

    plot_accuracy_cdf(
        all_pairs,
        "delta_accuracy",
        "Majority offset-delta prediction accuracy",
        output_dir / "offset_delta_predictability_cdf",
    )
    plot_accuracy_cdf(
        all_pairs,
        "size_accuracy",
        "Majority LD2 size prediction accuracy",
        output_dir / "ld2_size_predictability_cdf",
    )
    plot_unique_delta_histogram(all_pairs, output_dir / "unique_offset_delta_histogram")

    suite = reports[-1]
    print("\nSuite average (arithmetic mean across applications):")
    print(f"  offset delta highly predictable: {suite.delta_highly_predictable_frac:.1%}")
    print(f"  LD2 size highly predictable:      {suite.size_highly_predictable_frac:.1%}")
    print(
        "  dynamic-weighted predictable-or-better -- "
        f"offset delta: {suite.dynamic_delta_predictable_or_better_frac:.1%}  "
        f"LD2 size: {suite.dynamic_size_predictable_or_better_frac:.1%}"
    )
    print("\nOutputs:")
    for name in (
        "fusion_predictability_pair_summary.csv",
        "fusion_predictability_summary.csv",
        "fusion_predictability_computation_log.txt",
        "offset_delta_predictability_cdf.png",
        "ld2_size_predictability_cdf.png",
        "unique_offset_delta_histogram.png",
    ):
        print(f"  - {output_dir / name}")


if __name__ == "__main__":
    main()
