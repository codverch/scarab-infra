#!/usr/bin/env python3
"""Simpoint-weighted I-Fuse predictor accuracy on resolved prediction pairs.

Per workload:
  resolved predictions = IFUSE_CORRECT_PREDICTIONS + IFUSE_INCORRECT_PREDICTIONS
  accuracy (%) = 100 * sum(weight * IFUSE_CORRECT_PREDICTIONS)
                 / sum(weight * resolved predictions)

Only pairs where the predicted LOAD2 arrived and was judged correct/incorrect are
included. Unresolved LOAD1 predictions (e.g. invalidated before LOAD2) are excluded.

Also logs and plots weighted MPKI:
  MPKI = 1000 * sum(weight * IFUSE_INCORRECT_PREDICTIONS)
         / sum(weight * Periodic_Instructions)

Reads per-simpoint Scarab stat CSVs under:
  {simulations-root}/ifuse/ifuse/datacenter/datacenter/<workload>/<cluster_id>/

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ifuse_accuracy.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/ifuse_accuracy

cd /users/deepmish/scarab
git add src/hpca2027-main-graphs-results/ifuse_accuracy/
git commit -m "Update HPCA main-graph I-Fuse accuracy results."
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

from plot_ipc import (  # noqa: E402
    DEFAULT_IFUSE_ACCURACY_OUTPUT_DIR,
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

CORRECT_PRED_STAT = "IFUSE_CORRECT_PREDICTIONS_count"
INCORRECT_PRED_STAT = "IFUSE_INCORRECT_PREDICTIONS_count"
PERIODIC_INST_STAT = "Periodic_Instructions"


@dataclass
class AccuracyResult:
    workload: str
    correct_predictions: float
    resolved_predictions: float
    incorrect_predictions: float
    periodic_instructions: float
    accuracy_pct: float
    mpki: float
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


def simpoint_stat(
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


def compute_workload_accuracy(
    workload: str,
    ifuse_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    ifuse_config: str,
    suite: str,
    subsuite: str,
) -> AccuracyResult | None:
    weighted_correct = 0.0
    weighted_resolved = 0.0
    weighted_incorrect = 0.0
    weighted_instructions = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue

        correct = simpoint_stat(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file="ifuse.stat.0.csv",
            stat_name=CORRECT_PRED_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        incorrect = simpoint_stat(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file="ifuse.stat.0.csv",
            stat_name=INCORRECT_PRED_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        instructions = simpoint_stat(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file="core.stat.0.csv",
            stat_name=PERIODIC_INST_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        if (
            correct is None
            or incorrect is None
            or instructions is None
            or instructions <= 0
        ):
            continue

        resolved = correct + incorrect
        if resolved <= 0:
            continue

        weighted_correct += weight * correct
        weighted_resolved += weight * resolved
        weighted_incorrect += weight * incorrect
        weighted_instructions += weight * instructions
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_resolved <= 0 or weighted_instructions <= 0:
        return None

    return AccuracyResult(
        workload=workload,
        correct_predictions=weighted_correct,
        resolved_predictions=weighted_resolved,
        incorrect_predictions=weighted_incorrect,
        periodic_instructions=weighted_instructions,
        accuracy_pct=100.0 * weighted_correct / weighted_resolved,
        mpki=1000.0 * weighted_incorrect / weighted_instructions,
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[AccuracyResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "weighted_correct_predictions",
                "weighted_resolved_predictions",
                "weighted_incorrect_predictions",
                "weighted_periodic_instructions",
                "accuracy_pct",
                "mpki",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "weighted_correct_predictions": f"{result.correct_predictions:.1f}",
                    "weighted_resolved_predictions": f"{result.resolved_predictions:.1f}",
                    "weighted_incorrect_predictions": f"{result.incorrect_predictions:.1f}",
                    "weighted_periodic_instructions": f"{result.periodic_instructions:.1f}",
                    "accuracy_pct": f"{result.accuracy_pct:.4f}",
                    "mpki": f"{result.mpki:.6f}",
                }
            )


def write_computation_log(path: Path, results: list[AccuracyResult]) -> None:
    with path.open("w") as fh:
        fh.write("I-Fuse predictor accuracy on resolved pairs\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "resolved_predictions = IFUSE_CORRECT_PREDICTIONS_count "
            "+ IFUSE_INCORRECT_PREDICTIONS_count\n"
        )
        fh.write(
            "accuracy_pct = 100 * weighted(IFUSE_CORRECT_PREDICTIONS_count) "
            "/ weighted(resolved_predictions)\n"
        )
        fh.write(
            "mpki = 1000 * weighted(IFUSE_INCORRECT_PREDICTIONS_count) "
            "/ weighted(Periodic_Instructions)\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted resolved predictions: {result.resolved_predictions:.1f}\n")
            fh.write(f"  weighted correct:           {result.correct_predictions:.1f}\n")
            fh.write(f"  weighted incorrect:         {result.incorrect_predictions:.1f}\n")
            fh.write(f"  accuracy:                   {result.accuracy_pct:.4f}%\n")
            fh.write(f"  MPKI:                       {result.mpki:.6f}\n\n")

        if results:
            acc_avg = sum(r.accuracy_pct for r in results) / len(results)
            mpki_avg = sum(r.mpki for r in results) / len(results)
            fh.write(f"Arithmetic mean accuracy: {acc_avg:.4f}%\n")
            fh.write(f"Arithmetic mean MPKI:     {mpki_avg:.6f}\n")


def plot_accuracy_bars(results: list[AccuracyResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt

    accuracies = [r.accuracy_pct for r in results]
    arithmetic_mean = sum(accuracies) / len(accuracies)
    accuracies.append(arithmetic_mean)

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    width = 0.35

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ax.bar(
        x,
        accuracies,
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
        "I-Fuse predictor accuracy (%)",
        fontsize=26,
        fontfamily="serif",
    )

    lo = min(accuracies)
    hi = max(accuracies)
    span = hi - lo if hi > lo else max(abs(100.0 - hi), 0.01)
    pad = max(span * 0.35, 0.002)
    ax.set_ylim(max(0.0, lo - pad), min(100.0, hi + pad))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.3f}"))
    ax.tick_params(axis="y", labelsize=20)
    for label in ax.get_yticklabels():
        label.set_fontfamily("serif")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("ifuse_accuracy",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def plot_mpki_bars(results: list[AccuracyResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt

    mpki_values = [r.mpki for r in results]
    arithmetic_mean = sum(mpki_values) / len(mpki_values)
    mpki_values.append(arithmetic_mean)

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    width = 0.35

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ax.bar(
        x,
        mpki_values,
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
        "I-Fuse MPKI\n(mispredictions per kilo instructions)",
        fontsize=26,
        fontfamily="serif",
    )

    hi = max(mpki_values) if mpki_values else 1.0
    ax.set_ylim(0.0, hi * 1.12 + max(hi * 0.02, 0.0001))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.3f}"))
    ax.tick_params(axis="y", labelsize=20)
    for label in ax.get_yticklabels():
        label.set_fontfamily("serif")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("ifuse_mpki",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot simpoint-weighted I-Fuse predictor accuracy on resolved pairs and MPKI."
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--ifuse-config", default=DEFAULT_IFUSE_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/ifuse_accuracy)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    ifuse_dir = args.ifuse_dir or (sim_root / "ifuse")
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_IFUSE_ACCURACY_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing I-Fuse predictor accuracy...")
    print(f"  ifuse:  {ifuse_dir} (config={args.ifuse_config})")
    print(f"  output: {output_dir}")

    results: list[AccuracyResult] = []
    for workload in workloads:
        result = compute_workload_accuracy(
            workload,
            ifuse_dir,
            sp_weights,
            ifuse_config=args.ifuse_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing ifuse stats")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  accuracy={result.accuracy_pct:7.4f}%  "
            f"mpki={result.mpki:.6f}  (simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete I-Fuse accuracy data.")

    write_summary_csv(output_dir / "ifuse_accuracy_summary.csv", results)
    write_computation_log(output_dir / "ifuse_accuracy_computation_log.txt", results)
    plot_accuracy_bars(results, output_dir)
    plot_mpki_bars(results, output_dir)

    acc_avg = sum(r.accuracy_pct for r in results) / len(results)
    mpki_avg = sum(r.mpki for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  accuracy mean:     {acc_avg:.4f}%")
    print(f"  MPKI mean:         {mpki_avg:.6f}")
    print("\nOutputs:")
    print(f"  - {output_dir / 'ifuse_accuracy.png'}")
    print(f"  - {output_dir / 'ifuse_accuracy.pdf'}")
    print(f"  - {output_dir / 'ifuse_accuracy.eps'}")
    print(f"  - {output_dir / 'ifuse_mpki.png'}")
    print(f"  - {output_dir / 'ifuse_mpki.pdf'}")
    print(f"  - {output_dir / 'ifuse_mpki.eps'}")
    print(f"  - {output_dir / 'ifuse_accuracy_summary.csv'}")
    print(f"  - {output_dir / 'ifuse_accuracy_computation_log.txt'}")


if __name__ == "__main__":
    main()
