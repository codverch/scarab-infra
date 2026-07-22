#!/usr/bin/env python3
"""Simpoint-weighted fraction of on-path memory loads fused by I-Fuse and ideal fusion.

Each fusion pair involves two loads. FUSED_LOADS counters count pairs, so the load
numerator is doubled (ideal fusion also exposes IDEAL_FUSION_LOADS_PARTICIPATED_count).

Per workload:
  I-Fuse fraction  = 100 * sum(weight * 2 * IFUSE_FUSED_LOADS) / sum(weight * ONPATH_MEM_LOADS)
  Ideal fraction   = 100 * sum(weight * IDEAL_FUSION_LOADS_PARTICIPATED)
                     / sum(weight * ONPATH_MEM_LOADS)

ONPATH_MEM_LOADS is read from ideal-fusion simpoints (same trace ROI). Numerators come from
ifuse.stat.0.csv and ideal_fusion.stat.0.csv respectively.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_fusion_fraction.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/fusion_fraction

cd /users/deepmish/scarab
git add src/hpca2027-main-graphs-results/fusion_fraction/
git commit -m "Update HPCA main-graph fusion fraction results."
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
    DEFAULT_FUSION_FRACTION_OUTPUT_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IFUSE_CONFIG,
    DEFAULT_IFUSE_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IDEAL_FUSION_COLOR,
    IFUSE_COLOR,
    MAROON_COLOR,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

IFUSE_FUSED_STAT = "IFUSE_FUSED_LOADS_count"
IDEAL_LOADS_PARTICIPATED_STAT = "IDEAL_FUSION_LOADS_PARTICIPATED_count"
ONPATH_MEM_LOADS_STAT = "ONPATH_MEM_LOADS_count"
LOADS_PER_FUSION_PAIR = 2


@dataclass
class FusionFractionResult:
    workload: str
    ifuse_loads_participated: float
    ideal_loads_participated: float
    onpath_mem_loads: float
    ifuse_fraction_pct: float
    ideal_fraction_pct: float
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


def compute_workload_fraction(
    workload: str,
    ifuse_dir: Path,
    ideal_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    ifuse_config: str,
    ideal_config: str,
    suite: str,
    subsuite: str,
) -> FusionFractionResult | None:
    weighted_ifuse = 0.0
    weighted_ideal = 0.0
    weighted_onpath = 0.0
    weight_sum = 0.0
    trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue

        ifuse_fused = simpoint_stat(
            ifuse_dir,
            ifuse_config,
            workload,
            cluster_id,
            stat_file="ifuse.stat.0.csv",
            stat_name=IFUSE_FUSED_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        ideal_participated = simpoint_stat(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file="ideal_fusion.stat.0.csv",
            stat_name=IDEAL_LOADS_PARTICIPATED_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        onpath_loads = simpoint_stat(
            ideal_dir,
            ideal_config,
            workload,
            cluster_id,
            stat_file="ideal_fusion.stat.0.csv",
            stat_name=ONPATH_MEM_LOADS_STAT,
            suite=suite,
            subsuite=subsuite,
        )
        if (
            ifuse_fused is None
            or ideal_participated is None
            or onpath_loads is None
            or onpath_loads <= 0
        ):
            continue

        ifuse_participated = LOADS_PER_FUSION_PAIR * ifuse_fused

        weighted_ifuse += weight * ifuse_participated
        weighted_ideal += weight * ideal_participated
        weighted_onpath += weight * onpath_loads
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_onpath <= 0:
        return None

    return FusionFractionResult(
        workload=workload,
        ifuse_loads_participated=weighted_ifuse,
        ideal_loads_participated=weighted_ideal,
        onpath_mem_loads=weighted_onpath,
        ifuse_fraction_pct=100.0 * weighted_ifuse / weighted_onpath,
        ideal_fraction_pct=100.0 * weighted_ideal / weighted_onpath,
        trace_count=trace_count,
    )


def write_summary_csv(path: Path, results: list[FusionFractionResult]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "workload",
                "display_name",
                "trace_count",
                "weighted_ifuse_loads_participated",
                "weighted_ideal_loads_participated",
                "weighted_onpath_mem_loads",
                "ifuse_fraction_pct",
                "ideal_fraction_pct",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "workload": result.workload,
                    "display_name": rename_workload(result.workload),
                    "trace_count": result.trace_count,
                    "weighted_ifuse_loads_participated": f"{result.ifuse_loads_participated:.1f}",
                    "weighted_ideal_loads_participated": f"{result.ideal_loads_participated:.1f}",
                    "weighted_onpath_mem_loads": f"{result.onpath_mem_loads:.1f}",
                    "ifuse_fraction_pct": f"{result.ifuse_fraction_pct:.2f}",
                    "ideal_fraction_pct": f"{result.ideal_fraction_pct:.2f}",
                }
            )


def write_computation_log(path: Path, results: list[FusionFractionResult]) -> None:
    with path.open("w") as fh:
        fh.write("Fraction of on-path memory loads fused\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "fraction_pct = 100 * weighted(loads participating in fusion) "
            "/ weighted(ONPATH_MEM_LOADS_count)\n"
        )
        fh.write(
            "I-Fuse loads participating = 2 * IFUSE_FUSED_LOADS_count; "
            "ideal loads participating = IDEAL_FUSION_LOADS_PARTICIPATED_count.\n"
        )
        fh.write("ONPATH_MEM_LOADS read from ideal-fusion simpoints.\n\n")
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(f"  weighted on-path mem loads:      {result.onpath_mem_loads:.1f}\n")
            fh.write(
                f"  weighted ifuse loads fused:      {result.ifuse_loads_participated:.1f}  "
                f"({result.ifuse_fraction_pct:.2f}%)\n"
            )
            fh.write(
                f"  weighted ideal loads fused:    {result.ideal_loads_participated:.1f}  "
                f"({result.ideal_fraction_pct:.2f}%)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_fraction_pct for r in results) / len(results)
            ideal_avg = sum(r.ideal_fraction_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse fraction:  {ifuse_avg:.2f}%\n")
            fh.write(f"Arithmetic mean Ideal fraction:   {ideal_avg:.2f}%\n")


def plot_fusion_fraction_bars(results: list[FusionFractionResult], output_dir: Path) -> None:
    import matplotlib.pyplot as plt

    ifuse_pct = [r.ifuse_fraction_pct for r in results]
    ideal_pct = [r.ideal_fraction_pct for r in results]

    ifuse_mean = sum(ifuse_pct) / len(ifuse_pct)
    ideal_mean = sum(ideal_pct) / len(ideal_pct)
    ifuse_pct.append(ifuse_mean)
    ideal_pct.append(ideal_mean)

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    width = 0.18

    plt.rcParams.update({"font.size": 14, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    ax.bar(
        [i - 0.5 * width for i in x],
        ifuse_pct,
        width,
        label="I-Fuse",
        color=IFUSE_COLOR,
        edgecolor="black",
        linewidth=1.0,
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
        "Coverage of total on-path\n memory loads (%)",
        fontsize=26,
        fontfamily="serif",
    )
    ymax = max(ifuse_pct + ideal_pct)
    ax.set_ylim(0.0, min(100.0, ymax * 1.12 + 2.0))
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
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("fusion_fraction",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot fraction of on-path memory loads fused by I-Fuse and ideal fusion."
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
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/fusion_fraction)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    ifuse_dir = args.ifuse_dir or (sim_root / "ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion")
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_FUSION_FRACTION_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing fused on-path memory load fractions...")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    print(f"  output:       {output_dir}")

    results: list[FusionFractionResult] = []
    for workload in workloads:
        result = compute_workload_fraction(
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
            print(f"  skip {workload}: missing stats")
            continue
        results.append(result)
        print(
            f"  {workload:14s}  ifuse={result.ifuse_fraction_pct:5.2f}%  "
            f"ideal={result.ideal_fraction_pct:5.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete fusion-fraction data.")

    write_summary_csv(output_dir / "fusion_fraction_summary.csv", results)
    write_computation_log(output_dir / "fusion_fraction_computation_log.txt", results)
    plot_fusion_fraction_bars(results, output_dir)

    ifuse_avg = sum(r.ifuse_fraction_pct for r in results) / len(results)
    ideal_avg = sum(r.ideal_fraction_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    print(f"  I-Fuse mean:       {ifuse_avg:.2f}%")
    print(f"  Ideal fusion mean: {ideal_avg:.2f}%")
    print("\nOutputs:")
    print(f"  - {output_dir / 'fusion_fraction.png'}")
    print(f"  - {output_dir / 'fusion_fraction.pdf'}")
    print(f"  - {output_dir / 'fusion_fraction.eps'}")
    print(f"  - {output_dir / 'fusion_fraction_summary.csv'}")
    print(f"  - {output_dir / 'fusion_fraction_computation_log.txt'}")


if __name__ == "__main__":
    main()
