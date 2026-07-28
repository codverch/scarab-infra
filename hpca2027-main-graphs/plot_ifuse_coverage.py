#!/usr/bin/env python3
"""Simpoint-weighted Helios and I-Fuse coverage of ideal-fusion opportunities.

Coverage per workload (simpoint-weighted), with denominator selected by
`--denominator`:
  fused-loads:
    Helios: 100 * HELIOS_FUSIONS_COMMITTED / IDEAL_FUSION_FUSED_LOADS
    I-Fuse: 100 * IFUSE_CORRECT_PREDICTIONS / IDEAL_FUSION_FUSED_LOADS
  candidates:
    Helios: 100 * HELIOS_FUSIONS_COMMITTED / IDEAL_FUSION_CANDIDATE_ROWS
    I-Fuse: 100 * IFUSE_CORRECT_PREDICTIONS / IDEAL_FUSION_CANDIDATE_ROWS

Helios fusion counts are scaled to the ideal-fusion simpoint measurement window
when periodic instruction counts differ.

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ifuse_coverage.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/ifuse_coverage
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

from plot_fusion_predictability import DEFAULT_CANDIDATES_DIR, discover_simpoint_csvs  # noqa: E402
from plot_fusion_fraction import (  # noqa: E402
    HELIOS_FUSED_STAT,
    helios_stats_available,
    periodic_instructions,
    scale_to_measurement_window,
)
from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IDEAL_CONFIG,
    DEFAULT_IDEAL_DIR,
    DEFAULT_IFUSE_COVERAGE_OUTPUT_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    HELIOS_COLOR,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    order_workloads_by_group,
    rename_workload,
)

IFUSE_STAT = "IFUSE_CORRECT_PREDICTIONS_count"
IDEAL_STAT = "IDEAL_FUSION_FUSED_LOADS_count"
DENOMINATOR_FUSED_LOADS = "fused-loads"
DENOMINATOR_CANDIDATES = "candidates"

WORKLOAD_CANDIDATE_ALIASES: dict[str, tuple[str, ...]] = {
    "core_bench": ("core_bench", "corebench"),
}

SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
)

COVERAGE_BAR_WIDTH = 0.32


@dataclass
class CoverageResult:
    workload: str
    helios_loads: float | None
    ifuse_correct: float
    denominator_total: float
    helios_coverage_pct: float | None
    ifuse_coverage_pct: float
    trace_count: int
    helios_trace_count: int


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


def candidate_workload_names(workload: str) -> tuple[str, ...]:
    return WORKLOAD_CANDIDATE_ALIASES.get(workload, (workload,))


def count_candidate_rows(csv_path: Path) -> int:
    rows = 0
    with csv_path.open() as fh:
        next(fh, None)
        for _ in fh:
            rows += 1
    return rows


def load_candidate_row_count(
    candidates_dir: Path,
    workload: str,
    cluster_id: str,
) -> float | None:
    for candidate_workload in candidate_workload_names(workload):
        csv_path = candidates_dir / candidate_workload / f"{cluster_id}.csv"
        if csv_path.is_file():
            return float(count_candidate_rows(csv_path))
    return None


def compute_workload_coverage(
    workload: str,
    helios_dir: Path | None,
    ifuse_dir: Path,
    ideal_dir: Path,
    candidates_dir: Path | None,
    sp_weights: dict[tuple[str, str], float],
    *,
    helios_config: str,
    ifuse_config: str,
    ideal_config: str,
    include_helios: bool,
    denominator: str,
    suite: str,
    subsuite: str,
) -> CoverageResult | None:
    weighted_helios = 0.0
    weighted_ifuse = 0.0
    weighted_denominator = 0.0
    weight_sum = 0.0
    trace_count = 0
    helios_trace_count = 0

    for (wl, cluster_id), weight in sp_weights.items():
        if wl != workload or weight <= 0:
            continue

        ideal_sim = find_simpoint_dir(
            ideal_dir, ideal_config, workload, cluster_id, suite=suite, subsuite=subsuite
        )
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
        denominator_count: float | None
        if denominator == DENOMINATOR_CANDIDATES:
            if candidates_dir is None:
                raise SystemExit("Candidate denominator requested without --candidates-dir")
            denominator_count = load_candidate_row_count(candidates_dir, workload, cluster_id)
        else:
            denominator_count = load_simpoint_counts(
                ideal_dir,
                ideal_config,
                workload,
                cluster_id,
                stat_file="ideal_fusion.stat.0.csv",
                stat_name=IDEAL_STAT,
                suite=suite,
                subsuite=subsuite,
            )

        if denominator_count is None or ifuse_count is None or denominator_count <= 0:
            continue

        reference_periodic = periodic_instructions(ideal_sim)

        helios_loads: float | None = None
        if include_helios and helios_dir is not None:
            helios_sim = find_simpoint_dir(
                helios_dir, helios_config, workload, cluster_id, suite=suite, subsuite=subsuite
            )
            helios_fused = load_simpoint_counts(
                helios_dir,
                helios_config,
                workload,
                cluster_id,
                stat_file="core.stat.0.csv",
                stat_name=HELIOS_FUSED_STAT,
                suite=suite,
                subsuite=subsuite,
            )
            if helios_fused is not None:
                helios_fused = scale_to_measurement_window(
                    helios_fused,
                    periodic_instructions(helios_sim),
                    reference_periodic,
                )
                helios_loads = helios_fused
                weighted_helios += weight * helios_loads
                helios_trace_count += 1

        weighted_ifuse += weight * ifuse_count
        weighted_denominator += weight * denominator_count
        weight_sum += weight
        trace_count += 1

    if trace_count == 0 or weight_sum <= 0 or weighted_denominator <= 0:
        return None

    helios_coverage = (
        100.0 * weighted_helios / weighted_denominator
        if include_helios and helios_trace_count > 0
        else None
    )
    return CoverageResult(
        workload=workload,
        helios_loads=weighted_helios if helios_trace_count > 0 else None,
        ifuse_correct=weighted_ifuse,
        denominator_total=weighted_denominator,
        helios_coverage_pct=helios_coverage,
        ifuse_coverage_pct=100.0 * weighted_ifuse / weighted_denominator,
        trace_count=trace_count,
        helios_trace_count=helios_trace_count,
    )


def write_summary_csv(
    path: Path,
    results: list[CoverageResult],
    *,
    include_helios: bool,
    denominator: str,
) -> None:
    denominator_field = (
        "weighted_ideal_fused"
        if denominator == DENOMINATOR_FUSED_LOADS
        else "weighted_ideal_candidates"
    )
    fieldnames = [
        "workload",
        "display_name",
        "trace_count",
        "weighted_ifuse_correct",
        denominator_field,
        "ifuse_coverage_pct",
    ]
    if include_helios:
        fieldnames[3:3] = [
            "helios_trace_count",
            "weighted_helios_loads",
            "helios_coverage_pct",
        ]

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = {
                "workload": result.workload,
                "display_name": rename_workload(result.workload),
                "trace_count": result.trace_count,
                "weighted_ifuse_correct": f"{result.ifuse_correct:.1f}",
                denominator_field: f"{result.denominator_total:.1f}",
                "ifuse_coverage_pct": f"{result.ifuse_coverage_pct:.2f}",
            }
            if include_helios:
                row.update(
                    {
                        "helios_trace_count": result.helios_trace_count,
                        "weighted_helios_loads": (
                            f"{result.helios_loads:.1f}"
                            if result.helios_loads is not None
                            else ""
                        ),
                        "helios_coverage_pct": (
                            f"{result.helios_coverage_pct:.2f}"
                            if result.helios_coverage_pct is not None
                            else ""
                        ),
                    }
                )
            writer.writerow(row)


def write_computation_log(
    path: Path,
    results: list[CoverageResult],
    *,
    include_helios: bool,
    denominator: str,
) -> None:
    with path.open("w") as fh:
        fh.write("Coverage of ideal-fusion opportunities\n")
        fh.write("=" * 80 + "\n")
        denominator_label = (
            "IDEAL_FUSION_FUSED_LOADS_count"
            if denominator == DENOMINATOR_FUSED_LOADS
            else "ideal_fusion_candidate_rows"
        )
        if include_helios:
            fh.write(
                "Helios coverage = 100 * weighted(HELIOS_FUSIONS_COMMITTED_count) "
                f"/ weighted({denominator_label})\n"
            )
        fh.write(
            "I-Fuse coverage = 100 * weighted(IFUSE_CORRECT_PREDICTIONS_count) "
            f"/ weighted({denominator_label})\n\n"
        )
        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            fh.write(f"  simpoints: {result.trace_count}\n")
            fh.write(
                f"  weighted denominator ({denominator_label}): "
                f"{result.denominator_total:.1f}\n"
            )
            if include_helios and result.helios_coverage_pct is not None:
                fh.write(
                    f"  helios loads: {result.helios_loads:.1f}  "
                    f"({result.helios_coverage_pct:.2f}%, "
                    f"{result.helios_trace_count} simpoints)\n"
                )
            fh.write(
                f"  ifuse correct: {result.ifuse_correct:.1f}  "
                f"({result.ifuse_coverage_pct:.2f}%)\n\n"
            )

        if results:
            ifuse_avg = sum(r.ifuse_coverage_pct for r in results) / len(results)
            fh.write(f"Arithmetic mean I-Fuse coverage: {ifuse_avg:.2f}%\n")
            if include_helios:
                helios_vals = [
                    r.helios_coverage_pct
                    for r in results
                    if r.helios_coverage_pct is not None
                ]
                if helios_vals:
                    fh.write(
                        "Arithmetic mean Helios coverage: "
                        f"{sum(helios_vals) / len(helios_vals):.2f}%\n"
                    )


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * COVERAGE_BAR_WIDTH for i in range(n)]


def _tight_x_limits(ax, x_min: float, x_max: float, n_bars: int) -> None:
    half_span = (n_bars * COVERAGE_BAR_WIDTH) / 2.0
    ax.set_xlim(x_min - half_span - 0.12, x_max + half_span + 0.10)
    ax.margins(x=0)


def _legend_handles(*, include_helios: bool) -> list:
    from matplotlib.patches import Patch

    handles = []
    for key, label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        handles.append(
            Patch(
                facecolor=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                label=label,
            )
        )
    return handles


def plot_coverage_bars(
    results: list[CoverageResult],
    output_dir: Path,
    *,
    include_helios: bool,
    denominator: str,
) -> None:
    import matplotlib.pyplot as plt

    active_series: list[tuple[str, list[float], str]] = []
    for key, _label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        attr = "helios_coverage_pct" if key == "helios" else "ifuse_coverage_pct"
        values = [
            getattr(r, attr) if getattr(r, attr) is not None else float("nan")
            for r in results
        ]
        finite = [v for v in values if not math.isnan(v)]
        avg = sum(finite) / len(finite) if finite else float("nan")
        values.append(avg)
        active_series.append((key, values, color))

    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    offsets = _bar_offsets(len(active_series))

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
    fig, ax = plt.subplots(figsize=(24, 6.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (_name, values, color) in zip(offsets, active_series):
        ax.bar(
            [i + offset for i in x],
            [0.0 if math.isnan(val) else val for val in values],
            COVERAGE_BAR_WIDTH,
            color=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )

    if len(display_apps) > 1:
        ax.axvline(
            x=len(display_apps) - 1.5,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            alpha=1.0,
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
    for i, label in enumerate(ax.get_xticklabels()):
        if i == len(display_apps) - 1:
            label.set_weight("bold")

    _tight_x_limits(ax, x[0], x[-1], n_bars=len(active_series))

    ax.set_ylabel(
        (
            "Fraction of\ntotal ideal-fusion\ncandidates covered (%)"
            if denominator == DENOMINATOR_CANDIDATES
            else "Fraction of\nideally fused loads\ncovered (%)"
        ),
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.set_ylim(0.0, 100.0)
    import matplotlib.ticker as mticker

    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    plt.subplots_adjust(top=0.88, bottom=0.28, left=0.08, right=0.99)

    legend = ax.legend(
        handles=_legend_handles(include_helios=include_helios),
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.96),
        bbox_transform=ax.transAxes,
        borderaxespad=0.0,
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        ncol=len(active_series),
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
        framealpha=1.0,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    output_dir.mkdir(parents=True, exist_ok=True)
    stems = (
        ("ifuse_candidate_coverage", "ideal_fusion_candidate_coverage")
        if denominator == DENOMINATOR_CANDIDATES
        else ("ifuse_coverage", "ideal_fusion_coverage")
    )
    for stem in stems:
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.05, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.05, dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plot simpoint-weighted Helios and I-Fuse coverage of ideally fused loads."
        )
    )
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument(
        "--include-helios",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Plot Helios bars (default: on)",
    )
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--candidates-dir", type=Path, default=DEFAULT_CANDIDATES_DIR)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument("--ideal-fusion-config", default=DEFAULT_IDEAL_CONFIG)
    parser.add_argument(
        "--denominator",
        choices=(DENOMINATOR_FUSED_LOADS, DENOMINATOR_CANDIDATES),
        default=DENOMINATOR_FUSED_LOADS,
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    ideal_dir = args.ideal_fusion_dir or DEFAULT_IDEAL_DIR
    candidates_dir = args.candidates_dir
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_IFUSE_COVERAGE_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    plot_helios = False
    if args.include_helios:
        plot_helios = helios_stats_available(
            helios_dir,
            args.helios_config,
            workloads,
            sp_weights,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if not plot_helios:
            print(f"Helios skipped: missing {HELIOS_FUSED_STAT} in {helios_dir}")

    if args.denominator == DENOMINATOR_CANDIDATES and not candidates_dir.is_dir():
        raise SystemExit(f"Candidates directory does not exist: {candidates_dir}")

    print("Computing coverage of ideal-fusion opportunities...")
    if plot_helios:
        print(f"  helios:       {helios_dir} (config={args.helios_config})")
    print(f"  ifuse:        {ifuse_dir} (config={args.ifuse_config})")
    print(f"  ideal fusion: {ideal_dir} (config={args.ideal_fusion_config})")
    if args.denominator == DENOMINATOR_CANDIDATES:
        print(f"  candidates:   {candidates_dir}")
    print(f"  denominator:  {args.denominator}")
    print(f"  output:       {output_dir}")

    results: list[CoverageResult] = []
    for workload in workloads:
        result = compute_workload_coverage(
            workload,
            helios_dir if plot_helios else None,
            ifuse_dir,
            ideal_dir,
            candidates_dir if args.denominator == DENOMINATOR_CANDIDATES else None,
            sp_weights,
            helios_config=args.helios_config,
            ifuse_config=args.ifuse_config,
            ideal_config=args.ideal_fusion_config,
            include_helios=plot_helios,
            denominator=args.denominator,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing data for selected denominator")
            continue
        results.append(result)
        helios_str = (
            f"{result.helios_coverage_pct:6.2f}%"
            if result.helios_coverage_pct is not None
            else "   n/a"
        )
        print(
            f"  {workload:14s}  helios={helios_str}  "
            f"ifuse={result.ifuse_coverage_pct:6.2f}%  "
            f"(simpoints={result.trace_count})"
        )

    if not results:
        raise SystemExit("No workloads with complete coverage data for the selected denominator.")

    results_by_workload = {r.workload: r for r in results}
    results = [
        results_by_workload[wl]
        for wl in order_workloads_by_group(list(results_by_workload))
    ]

    write_summary_csv(
        output_dir / "ifuse_coverage_summary.csv",
        results,
        include_helios=plot_helios,
        denominator=args.denominator,
    )
    write_computation_log(
        output_dir / "ifuse_coverage_computation_log.txt",
        results,
        include_helios=plot_helios,
        denominator=args.denominator,
    )
    plot_coverage_bars(
        results,
        output_dir,
        include_helios=plot_helios,
        denominator=args.denominator,
    )

    ifuse_avg = sum(r.ifuse_coverage_pct for r in results) / len(results)
    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    if plot_helios:
        helios_vals = [r.helios_coverage_pct for r in results if r.helios_coverage_pct is not None]
        if helios_vals:
            print(f"  Helios mean:       {sum(helios_vals) / len(helios_vals):.2f}%")
    print(f"  I-Fuse mean:       {ifuse_avg:.2f}%")
    print("\nOutputs:")
    if args.denominator == DENOMINATOR_CANDIDATES:
        print(f"  - {output_dir / 'ifuse_candidate_coverage.png'}")
        print(f"  - {output_dir / 'ideal_fusion_candidate_coverage.png'}")
    else:
        print(f"  - {output_dir / 'ifuse_coverage.png'}")
        print(f"  - {output_dir / 'ideal_fusion_coverage.png'}")
    print(f"  - {output_dir / 'ifuse_coverage_summary.csv'}")
    print(f"  - {output_dir / 'ifuse_coverage_computation_log.txt'}")


if __name__ == "__main__":
    main()
