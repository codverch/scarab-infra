#!/usr/bin/env python3
"""Simpoint-weighted I-Fuse coverage of ideal-fusion candidates.

Coverage per workload:
  100 * sum(weight * IFUSE_CORRECT_PREDICTIONS) / sum(weight * IDEAL_FUSION_FUSED_LOADS)

Reads per-simpoint Scarab stat CSVs under:
  {simulations-root}/ifuse/ifuse/datacenter/datacenter/<workload>/<cluster_id>/ifuse.stat.0.csv
  {simulations-root}/ideal-fusion/ideal-fusion/datacenter/datacenter/.../ideal_fusion.stat.0.csv

Example:
  /users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
    hpca2027-main-graphs/plot_ifuse_coverage.py \\
    --simulations-root /users/deepmish/scarab/src/simulations \\
    --output-dir /users/deepmish/scarab-infra/hpca2027-main-graphs/output
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IFUSE_CONFIG,
    DEFAULT_IFUSE_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IFUSE_COLOR,
    MAROON_COLOR,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

IFUSE_STAT = "IFUSE_CORRECT_PREDICTIONS_count"
IDEAL_STAT = "IDEAL_FUSION_FUSED_LOADS_count"


@dataclass
class CoverageResult:
    workload: str
    ifuse_correct: float
    ideal_fused: float
    coverage_pct: float
    trace_count: int


def stat_count_from_csv(stat_csv: Path, stat_name: str) -> float | None:
    if not stat_csv.is_file():
        return None
    with stat_csv.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            if row[0].strip() == stat_name:
                try:
                    return float(row[2].strip())
                except ValueError:
                    return None
    return None


def load_simpoint_counts(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
    *,
    stat_file: str,
    stat_name: str,
    suite: str,
    subsuite: str,
) -> float | None:
    sim_dir = find_simpoint_dir(
        experiment_dir, config, workload, cluster_id, suite=suite, subsuite=subsuite
    )
    if sim_dir is None:
        return None
    return stat_count_from_csv(sim_dir / stat_file, stat_name)


def compute_workload_coverage(
    workload: str,
    ifuse_dir: Path,
    ideal_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    ifuse_config: str,
    ideal_config: str,
    suite: str,
    subsuite: str,
) -> CoverageResult | None:
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue

        ifuse_count = load_simpoint_counts(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file="ifuse.stat.0.csv",
            stat_name=IFUSE_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_count = load_simpoint_counts(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file="ideal_fusion.stat.0.csv",
            stat_name=IDEAL_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        if ifuse_count is None or ideal_count is None:
            continue

        weighted_ifuse += weight * ifuse_count
        weighted_ideal += weight * ideal_count
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_ideal <= 0:
        return None

    return CoverageResult(
        workload=workload,
        ifuse_correct=weighted_ifuse,
        ideal_fused=weighted_ideal,
        coverage_pct=100.0 * weighted_ifuse / weighted_ideal,
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[CoverageResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "weighted_ifuse_correct",
                "weighted_ideal_fused",
                "coverage_pct",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "weighted_ifuse_correct": f"{result.ifuse_correct:.1f}",
                    "weighted_ideal_fused": f"{result.ideal_fused:.1f}",
                    "coverage_pct": f"{result.coverage_pct:.2f}",
                }
            )


def write_computation_log(path: Path, results: list[CoverageResult]) -> None:
    with path.open("w") as fh:
        fh.write("I-Fuse coverage of ideal-fusion candidates\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "coverage_pct = 100 * weighted(IFUSE_CORRECT_PREDICTIONS_count) "
            "/ weighted(IDEAL_FUSION_FUSED_LOADS_count)\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted ifuse correct: {result.ifuse_correct:.1f}\n")
            fh.write(f"  weighted ideal fused:   {result.ideal_fused:.1f}\n")
            fh.write(f"  coverage:               {result.coverage_pct:.2f}%\n\n")

        if results:
            avg = sum(r.coverage_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean coverage: {avg:.2f}%\n")


def plot_coverage_bars(results: list[CoverageResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt

    coverages = [r.coverage_pct for r in results]
    arithmetic_mean = sum(coverages) / len(coverages)
    coverages.append(arithmetic_mean)

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    width = 0.35

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ax.bar(
        x,
        coverages,
        width,
        color=IFUSE_COLOR,
        edgecolor="black",
        linewidth=1.0,
        zorder=3,
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
        "I-Fuse coverage (%)\n(of ideal-fusion candidates)",
        fontsize=26,
        fontfamily="serif",
    )
    ymax = max(coverages)
    ax.set_ylim(0.0, min(100.0, ymax * 1.12 + 2.0))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="y", labelsize=20)
    for label in ax.get_yticklabels():
        label.set_fontfamily("serif")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("ifuse_coverage",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot simpoint-weighted I-Fuse coverage of ideal-fusion candidates."
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--ifuse-config", default=DEFAULT_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: hpca2027-main-graphs/output)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    ifuse_dir = args.ifuse_dir or (sim_root / "ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion")
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or (GRAPH_DIR / "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing I-Fuse coverage...")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    results: list[CoverageResult] = []
    for workload in workloads:
        result = compute_workload_coverage(
            workload,
            ifuse_dir,
            ideal_dir,
            sp_weights,
            ifuse_config=args.ifuse_config,
            ideal_config=args.ideal_fusion_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing ifuse/ideal-fusion stats")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  coverage={result.coverage_pct:6.2f}%  "
            f"(ifuse={result.ifuse_correct:.0f}, ideal={result.ideal_fused:.0f}, "
            f"simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete I-Fuse and ideal-fusion coverage data.")

    write_summary_csv(output_dir / "ifuse_coverage_summary.csv", results)
    write_computation_log(output_dir / "ifuse_coverage_computation_log.txt", results)
    plot_coverage_bars(results, output_dir)

    avg = sum(r.coverage_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  arithmetic mean:   {avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'ifuse_coverage.png'}")
    print(f"  - {output_dir / 'ifuse_coverage.pdf'}")
    print(f"  - {output_dir / 'ifuse_coverage.eps'}")
    print(f"  - {output_dir / 'ifuse_coverage_summary.csv'}")
    print(f"  - {output_dir / 'ifuse_coverage_computation_log.txt'}")


if __name__ == "__main__":
    main()
