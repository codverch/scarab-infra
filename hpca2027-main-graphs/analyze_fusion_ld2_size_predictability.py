#!/usr/bin/env python3
"""LD2 memory-size predictability for static fusible (LD1 PC, LD2 PC) pairs.

Ideal-fusion candidate dumps list one row per *dynamic* fusible load pair
observed during Scarab pass-1.  Many dynamic instances collapse onto the same
*static* pair (load1_pc, load2_pc).  For each static pair this script:

  1. Chooses the pair's SimPoint-weighted *dominant offset delta*:
         offset_delta = load2_block_offset - load1_block_offset
     The dominant delta is the value a fusion predictor would guess after
     weight-averaging per-simpoint histograms (same model as
     plot_fusion_predictability.py).

  2. Asks whether LD2's memory access size (load2_mem_size) is *predictable*
     for that static pair.  Again we use a majority-value predictor evaluated
     with SimPoint weights: size_accuracy is the weighted fraction of dynamic
     instances whose LD2 size equals the dominant size.

The main question answered here is quantitative:
  "Among all static fusible PC pairs (each with its chosen dominant offset
   delta), how many also have a predictable LD2 memory size?"

Thresholds (paper bands, shared with plot_fusion_predictability.py):
  - highly predictable: size_accuracy >= 95%
  - predictable:        size_accuracy >= 80%
  - unpredictable:      size_accuracy <  80%

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
  /users/deepmish/scarab-infra/hpca2027-main-graphs/analyze_fusion_ld2_size_predictability.py \\
  --candidates-dir /dev/shm/baseline/ideal_fusion_candidates \\
  --trace-root /dev/shm/baseline/simpoint_traces \\
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/fusion_predictability
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_fusion_predictability import (  # noqa: E402
    CANDIDATE_WORKLOADS,
    DEFAULT_CANDIDATES_DIR,
    DEFAULT_OUTPUT_DIR,
    HIGHLY_PREDICTABLE_THRESHOLD,
    PREDICTABLE_THRESHOLD,
    PairAccuracy,
    classify,
    compute_workload_pairs,
)
from plot_ipc import (  # noqa: E402
    DEFAULT_TRACE_ROOT,
    load_simpoint_trace_weights,
    rename_workload,
)

# Output filenames (written under --output-dir).
PAIR_TABLE_CSV = "fusion_ld2_size_pair_table.csv"
SUMMARY_CSV = "fusion_ld2_size_summary.csv"
REPORT_TXT = "fusion_ld2_size_report.txt"


@dataclass(frozen=True)
class WorkloadSizeReport:
    """How many static pairs have predictable LD2 size in one workload."""

    workload: str
    num_pairs: int
    highly_predictable: int
    predictable_or_better: int
    unpredictable: int

    @property
    def highly_predictable_frac(self) -> float:
        return self.highly_predictable / self.num_pairs if self.num_pairs else float("nan")

    @property
    def predictable_or_better_frac(self) -> float:
        return self.predictable_or_better / self.num_pairs if self.num_pairs else float("nan")


def summarize_ld2_size_predictability(workload: str, pairs: list[PairAccuracy]) -> WorkloadSizeReport:
    """Count pairs whose LD2 size majority predictor meets each threshold."""
    highly = sum(1 for p in pairs if p.size_accuracy >= HIGHLY_PREDICTABLE_THRESHOLD)
    predictable = sum(1 for p in pairs if p.size_accuracy >= PREDICTABLE_THRESHOLD)
    return WorkloadSizeReport(
        workload=workload,
        num_pairs=len(pairs),
        highly_predictable=highly,
        predictable_or_better=predictable,
        unpredictable=len(pairs) - predictable,
    )


def write_pair_table_csv(output_dir: Path, pairs: list[PairAccuracy]) -> Path:
    """One row per static pair: PCs, chosen dominant delta, LD2-size predictability."""
    path = output_dir / PAIR_TABLE_CSV
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "load1_pc",
                "load2_pc",
                "dominant_offset_delta",
                "delta_accuracy",
                "raw_unique_deltas",
                "ld2_size_accuracy",
                "dominant_ld2_mem_size",
                "ld2_size_class",
                "raw_observations",
                "dynamic_weight",
            ]
        )
        for pair in sorted(
            pairs,
            key=lambda p: (p.workload, p.load1_pc, p.load2_pc),
        ):
            writer.writerow(
                [
                    pair.workload,
                    pair.load1_pc,
                    pair.load2_pc,
                    pair.dominant_delta,
                    f"{pair.delta_accuracy:.6f}",
                    pair.raw_unique_deltas,
                    f"{pair.size_accuracy:.6f}",
                    pair.dominant_size,
                    classify(pair.size_accuracy),
                    pair.raw_observations,
                    f"{pair.dynamic_weight:.6f}",
                ]
            )
    return path


def write_summary_csv(output_dir: Path, reports: list[WorkloadSizeReport]) -> Path:
    path = output_dir / SUMMARY_CSV
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "workload",
                "display_name",
                "num_pairs",
                "ld2_size_highly_predictable",
                "ld2_size_highly_predictable_frac",
                "ld2_size_predictable_or_better",
                "ld2_size_predictable_or_better_frac",
                "ld2_size_unpredictable",
                "ld2_size_unpredictable_frac",
            ]
        )
        for report in reports:
            display = (
                report.workload
                if report.workload == "Suite average"
                else rename_workload(report.workload)
            )
            writer.writerow(
                [
                    report.workload,
                    display,
                    report.num_pairs,
                    report.highly_predictable,
                    f"{report.highly_predictable_frac:.4f}",
                    report.predictable_or_better,
                    f"{report.predictable_or_better_frac:.4f}",
                    report.unpredictable,
                    f"{1.0 - report.predictable_or_better_frac:.4f}",
                ]
            )
    return path


def write_text_report(
    output_dir: Path,
    reports: list[WorkloadSizeReport],
    *,
    highly_threshold: float,
    predictable_threshold: float,
) -> Path:
    path = output_dir / REPORT_TXT
    with path.open("w") as fh:
        fh.write("LD2 memory-size predictability for static fusible PC pairs\n")
        fh.write("=" * 72 + "\n\n")
        fh.write(
            "For each static pair (load1_pc, load2_pc) we first choose the "
            "SimPoint-weighted dominant cache-block offset delta, then measure "
            "whether LD2's memory access size is predictable via a majority-value "
            "predictor (same methodology as plot_fusion_predictability.py).\n\n"
        )
        fh.write(
            f"Highly predictable: ld2_size_accuracy >= {highly_threshold:.0%}\n"
            f"Predictable:        ld2_size_accuracy >= {predictable_threshold:.0%}\n"
            f"Unpredictable:      ld2_size_accuracy <  {predictable_threshold:.0%}\n\n"
        )
        for report in reports:
            label = (
                "Suite average"
                if report.workload == "Suite average"
                else f"{report.workload} ({rename_workload(report.workload)})"
            )
            fh.write(f"{label}\n")
            fh.write(f"  static PC pairs: {report.num_pairs}\n")
            fh.write(
                f"  LD2 size highly predictable:    "
                f"{report.highly_predictable:6d}  ({report.highly_predictable_frac:.1%})\n"
            )
            fh.write(
                f"  LD2 size predictable-or-better: "
                f"{report.predictable_or_better:6d}  ({report.predictable_or_better_frac:.1%})\n"
            )
            fh.write(
                f"  LD2 size unpredictable:        "
                f"{report.unpredictable:6d}  ({1.0 - report.predictable_or_better_frac:.1%})\n\n"
            )
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "For each static fusible (LD1 PC, LD2 PC) pair, choose the dominant "
            "offset delta and report how many pairs have predictable LD2 memory size."
        )
    )
    parser.add_argument(
        "--candidates-dir",
        type=Path,
        default=DEFAULT_CANDIDATES_DIR,
        help="Pass-1 ideal-fusion candidate root: {workload}/{cluster_id}.csv",
    )
    parser.add_argument(
        "--trace-root",
        type=Path,
        default=DEFAULT_TRACE_ROOT,
        help="SimPoint trace bundles (for cluster weights).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV and text outputs.",
    )
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
    reports: list[WorkloadSizeReport] = []

    for workload in workloads:
        print(f"Processing {workload}...", flush=True)
        pairs = compute_workload_pairs(workload, candidates_dir, sp_weights)
        if not pairs:
            print(f"  skip {workload}: no candidate pairs found", flush=True)
            continue
        all_pairs.extend(pairs)
        report = summarize_ld2_size_predictability(workload, pairs)
        reports.append(report)
        print(
            f"  {workload:14s}  pairs={report.num_pairs:6d}  "
            f"LD2 size predictable-or-better={report.predictable_or_better_frac:.1%}",
            flush=True,
        )

    if not reports:
        raise SystemExit("No workloads produced candidate pairs.")

    # Suite row uses exact counts from pooled pairs for transparency.
    pooled = summarize_ld2_size_predictability("Suite average", all_pairs)
    reports.append(pooled)

    output_dir.mkdir(parents=True, exist_ok=True)
    pair_path = write_pair_table_csv(output_dir, all_pairs)
    summary_path = write_summary_csv(output_dir, reports)
    report_path = write_text_report(
        output_dir,
        reports,
        highly_threshold=HIGHLY_PREDICTABLE_THRESHOLD,
        predictable_threshold=PREDICTABLE_THRESHOLD,
    )

    suite = reports[-1]
    print("\nSuite total (all static pairs pooled):")
    print(f"  static PC pairs:                 {suite.num_pairs}")
    print(f"  LD2 size highly predictable:    {suite.highly_predictable} ({suite.highly_predictable_frac:.1%})")
    print(
        f"  LD2 size predictable-or-better:  {suite.predictable_or_better} "
        f"({suite.predictable_or_better_frac:.1%})"
    )
    print("\nOutputs:")
    for path in (pair_path, summary_path, report_path):
        print(f"  - {path}")


if __name__ == "__main__":
    main()
