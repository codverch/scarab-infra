#!/usr/bin/env python3
"""Simpoint-weighted fusion-predictor accuracy (%) and MPKI for Helios, RFP, and I-Fuse.

Per workload and scheme, on resolved prediction pairs:
  accuracy (%) = 100 * weighted(correct) / weighted(correct + incorrect)
  MPKI         = 1000 * weighted(incorrect) / weighted(Periodic_Instructions)

Stats:
  Helios:  HELIOS_FUSIONS_count, HELIOS_FUSION_MISPREDICT_count (core.stat.0.csv)
  RFP:     RFP_RETIRE_PRED_CORRECT_count, RFP_RETIRE_PRED_WRONG_count (rfp.stat.0.csv)
  I-Fuse:  IFUSE_CORRECT_PREDICTIONS_count, IFUSE_INCORRECT_PREDICTIONS_count (ifuse.stat.0.csv)

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_ifuse_accuracy.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --helios-dir /users/deepmish/scarab/src/simulations/helios \
  --rfp-dir /users/deepmish/scarab/src/simulations/rfp \
  --ifuse-dir /users/deepmish/scarab/src/simulations/ifuse \
  --ifuse-config datacenter \
  --output-dir /users/deepmish/scarab/src/hpca2027-main-graphs-results/ifuse_accuracy

cd /users/deepmish/scarab
git add src/hpca2027-main-graphs-results/ifuse_accuracy/
git commit -m "Update HPCA main-graph predictor accuracy results."
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
    AVERAGE_SEPARATOR_COLOR,
    BAR_EDGE_WIDTH,
    BAR_WIDTH,
    DEFAULT_HELIOS_CONFIG,
    DEFAULT_HELIOS_DIR,
    DEFAULT_IFUSE_ACCURACY_OUTPUT_DIR,
    DEFAULT_IPC_IFUSE_CONFIG,
    DEFAULT_IPC_IFUSE_DIR,
    DEFAULT_RFP_CONFIG,
    DEFAULT_RFP_DIR,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    FONT_FAMILY,
    HELIOS_COLOR,
    IFUSE_COLOR,
    IPC_AXIS_FONT,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    load_simpoint_trace_weights,
    rename_workload,
)

PERIODIC_INST_STAT = "Periodic_Instructions"

HELIOS_CORRECT_STAT = "HELIOS_FUSIONS_count"
HELIOS_INCORRECT_STAT = "HELIOS_FUSION_MISPREDICT_count"

RFP_CORRECT_STAT = "RFP_RETIRE_PRED_CORRECT_count"
RFP_INCORRECT_STAT = "RFP_RETIRE_PRED_WRONG_count"

IFUSE_CORRECT_STAT = "IFUSE_CORRECT_PREDICTIONS_count"
IFUSE_INCORRECT_STAT = "IFUSE_INCORRECT_PREDICTIONS_count"

SERIES: tuple[tuple[str, str, str], ...] = (
    ("helios", "Helios", HELIOS_COLOR),
    ("rfp", "RFP", RFP_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
)


@dataclass
class SchemeAccuracy:
    correct: float
    resolved: float
    incorrect: float
    periodic_instructions: float
    accuracy_pct: float
    mpki: float
    trace_count: int


@dataclass
class AccuracyResult:
    workload: str
    helios: SchemeAccuracy | None
    rfp: SchemeAccuracy | None
    ifuse: SchemeAccuracy


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


def compute_scheme_accuracy(
    workload: str,
    experiment_dir: Path,
    config: str,
    sp_weights: dict[tuple[str, str], float],
    *,
    correct_stat: str,
    incorrect_stat: str,
    stat_file: str,
    instructions_file: str = "core.stat.0.csv",
    suite: str,
    subsuite: str,
) -> SchemeAccuracy | None:
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
            experiment_dir,
            config,
            workload,
            cluster_id,
            stat_file=stat_file,
            stat_name=correct_stat,
            suite=suite,
            subsuite=subsuite,
        )
        incorrect = simpoint_stat(
            experiment_dir,
            config,
            workload,
            cluster_id,
            stat_file=stat_file,
            stat_name=incorrect_stat,
            suite=suite,
            subsuite=subsuite,
        )
        instructions = simpoint_stat(
            experiment_dir,
            config,
            workload,
            cluster_id,
            stat_file=instructions_file,
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

    return SchemeAccuracy(
        correct=weighted_correct,
        resolved=weighted_resolved,
        incorrect=weighted_incorrect,
        periodic_instructions=weighted_instructions,
        accuracy_pct=100.0 * weighted_correct / weighted_resolved,
        mpki=1000.0 * weighted_incorrect / weighted_instructions,
        trace_count=trace_count,
    )


def compute_workload_accuracy(
    workload: str,
    helios_dir: Path,
    rfp_dir: Path,
    ifuse_dir: Path,
    sp_weights: dict[tuple[str, str], float],
    *,
    helios_config: str,
    rfp_config: str,
    ifuse_config: str,
    suite: str,
    subsuite: str,
) -> AccuracyResult | None:
    ifuse = compute_scheme_accuracy(
        workload,
        ifuse_dir,
        ifuse_config,
        sp_weights,
        correct_stat=IFUSE_CORRECT_STAT,
        incorrect_stat=IFUSE_INCORRECT_STAT,
        stat_file="ifuse.stat.0.csv",
        suite=suite,
        subsuite=subsuite,
    )
    if ifuse is None:
        return None

    helios = compute_scheme_accuracy(
        workload,
        helios_dir,
        helios_config,
        sp_weights,
        correct_stat=HELIOS_CORRECT_STAT,
        incorrect_stat=HELIOS_INCORRECT_STAT,
        stat_file="core.stat.0.csv",
        suite=suite,
        subsuite=subsuite,
    )
    rfp = compute_scheme_accuracy(
        workload,
        rfp_dir,
        rfp_config,
        sp_weights,
        correct_stat=RFP_CORRECT_STAT,
        incorrect_stat=RFP_INCORRECT_STAT,
        stat_file="rfp.stat.0.csv",
        suite=suite,
        subsuite=subsuite,
    )

    return AccuracyResult(workload=workload, helios=helios, rfp=rfp, ifuse=ifuse)


def write_summary_csv(
    path: Path,
    results: list[AccuracyResult],
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    fieldnames = ["workload", "display_name"]
    for key, label, _color in SERIES:
        if key == "helios" and not include_helios:
            continue
        if key == "rfp" and not include_rfp:
            continue
        fieldnames.extend(
            [
                f"{key}_trace_count",
                f"{key}_accuracy_pct",
                f"{key}_mpki",
            ]
        )

    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row: dict[str, object] = {
                "workload": result.workload,
                "display_name": rename_workload(result.workload),
            }
            for key, _label, _color in SERIES:
                scheme = getattr(result, key)
                if key == "helios" and not include_helios:
                    continue
                if key == "rfp" and not include_rfp:
                    continue
                if scheme is None:
                    row[f"{key}_trace_count"] = ""
                    row[f"{key}_accuracy_pct"] = ""
                    row[f"{key}_mpki"] = ""
                else:
                    row[f"{key}_trace_count"] = scheme.trace_count
                    row[f"{key}_accuracy_pct"] = f"{scheme.accuracy_pct:.4f}"
                    row[f"{key}_mpki"] = f"{scheme.mpki:.6f}"
            writer.writerow(row)


def write_computation_log(
    path: Path,
    results: list[AccuracyResult],
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    with path.open("w") as fh:
        fh.write("Fusion predictor accuracy on resolved pairs\n")
        fh.write("=" * 80 + "\n")
        fh.write(
            "accuracy_pct = 100 * weighted(correct) / weighted(correct + incorrect)\n"
        )
        fh.write(
            "mpki = 1000 * weighted(incorrect) / weighted(Periodic_Instructions)\n\n"
        )
        if include_helios:
            fh.write(
                f"Helios: correct={HELIOS_CORRECT_STAT}, "
                f"incorrect={HELIOS_INCORRECT_STAT}\n"
            )
        if include_rfp:
            fh.write(
                f"RFP: correct={RFP_CORRECT_STAT}, incorrect={RFP_INCORRECT_STAT}\n"
            )
        fh.write(
            f"I-Fuse: correct={IFUSE_CORRECT_STAT}, incorrect={IFUSE_INCORRECT_STAT}\n\n"
        )

        for result in results:
            fh.write(f"{result.workload} ({rename_workload(result.workload)})\n")
            for key, label, _color in SERIES:
                if key == "helios" and not include_helios:
                    continue
                if key == "rfp" and not include_rfp:
                    continue
                scheme = getattr(result, key)
                if scheme is None:
                    fh.write(f"  {label}: n/a\n")
                    continue
                fh.write(
                    f"  {label}: accuracy={scheme.accuracy_pct:.4f}%  "
                    f"mpki={scheme.mpki:.6f}  (simpoints={scheme.trace_count})\n"
                )
            fh.write("\n")


def _bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]


def _format_y_tick(y: float, _p: int) -> str:
    if abs(y - round(y)) < 1e-9:
        return f"{int(round(y))}"
    return f"{y:.3f}".rstrip("0").rstrip(".")


def _style_legend(ax, *, include_helios: bool, include_rfp: bool, ncol: int) -> None:
    legend = ax.legend(
        handles=_legend_handles(include_helios=include_helios, include_rfp=include_rfp),
        loc="upper center",
        bbox_to_anchor=(0.5, 1.22),
        ncol=ncol,
        frameon=True,
        fancybox=False,
        shadow=False,
        edgecolor="black",
        framealpha=1.0,
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)


def _legend_handles(*, include_helios: bool, include_rfp: bool) -> list:
    from matplotlib.patches import Patch

    handles = []
    for key, label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        if key == "rfp" and not include_rfp:
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


def _series_values(
    results: list[AccuracyResult],
    attr: str,
    *,
    include_helios: bool,
    include_rfp: bool,
) -> list[tuple[str, list[float], str]]:
    active: list[tuple[str, list[float], str]] = []
    for key, _label, color in SERIES:
        if key == "helios" and not include_helios:
            continue
        if key == "rfp" and not include_rfp:
            continue
        values: list[float] = []
        for result in results:
            scheme = getattr(result, key)
            if scheme is None:
                values.append(float("nan"))
            else:
                values.append(getattr(scheme, attr))
        finite = [v for v in values if not math.isnan(v)]
        avg = sum(finite) / len(finite) if finite else float("nan")
        values.append(avg)
        active.append((key, values, color))
    return active


def plot_accuracy_bars(
    results: list[AccuracyResult],
    output_dir: Path,
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    import matplotlib.pyplot as plt

    active_series = _series_values(
        results, "accuracy_pct", include_helios=include_helios, include_rfp=include_rfp
    )
    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    offsets = _bar_offsets(len(active_series))

    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "font.size": IPC_AXIS_FONT,
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
            "legend.fontsize": IPC_AXIS_FONT,
        }
    )
    fig, ax = plt.subplots(figsize=(22, 8))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (_name, values, color) in zip(offsets, active_series):
        ax.bar(
            [i + offset for i in x],
            [0.0 if math.isnan(val) else val for val in values],
            BAR_WIDTH,
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
            alpha=0.9,
            linewidth=2.5,
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

    ax.set_ylabel("Predictor accuracy (%)", fontsize=IPC_AXIS_LABEL_FONT, fontfamily=FONT_FAMILY)

    finite = [
        v
        for _k, values, _c in active_series
        for v in values
        if not math.isnan(v)
    ]
    lo = min(finite) if finite else 0.0
    hi = max(finite) if finite else 100.0
    span = hi - lo if hi > lo else max(abs(100.0 - hi), 0.01)
    pad = max(span * 0.35, 0.002)
    ax.set_ylim(max(0.0, lo - pad), min(100.0, hi + pad))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_format_y_tick))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    fig.subplots_adjust(top=0.80)
    _style_legend(
        ax,
        include_helios=include_helios,
        include_rfp=include_rfp,
        ncol=len(active_series),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("predictor_accuracy", "ifuse_accuracy"):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def plot_mpki_bars(
    results: list[AccuracyResult],
    output_dir: Path,
    *,
    include_helios: bool,
    include_rfp: bool,
) -> None:
    import matplotlib.pyplot as plt

    active_series = _series_values(
        results, "mpki", include_helios=include_helios, include_rfp=include_rfp
    )
    display_apps = [rename_workload(r.workload) for r in results] + ["Average"]
    x = list(range(len(display_apps)))
    offsets = _bar_offsets(len(active_series))

    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.serif": [FONT_FAMILY, "DejaVu Serif", "serif"],
            "font.size": IPC_AXIS_FONT,
            "axes.labelsize": IPC_AXIS_LABEL_FONT,
            "xtick.labelsize": IPC_TICK_FONT,
            "ytick.labelsize": IPC_TICK_FONT,
            "legend.fontsize": IPC_AXIS_FONT,
        }
    )
    fig, ax = plt.subplots(figsize=(22, 5.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (_name, values, color) in zip(offsets, active_series):
        ax.bar(
            [i + offset for i in x],
            [0.0 if math.isnan(val) else val for val in values],
            BAR_WIDTH,
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
            alpha=0.9,
            linewidth=2.5,
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

    ax.set_ylabel(
        "Mispredictions\nPer Kilo Instructions\n(MPKI)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )

    finite = [
        v
        for _k, values, _c in active_series
        for v in values
        if not math.isnan(v)
    ]
    hi = max(finite) if finite else 1.0
    ax.set_ylim(0.0, hi * 1.12 + max(hi * 0.02, 0.0001))
    import matplotlib.ticker as mticker

    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_format_y_tick))
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.subplots_adjust(top=0.88, bottom=0.28, left=0.08, right=0.99)

    legend_handles = _legend_handles(include_helios=include_helios, include_rfp=include_rfp)
    legend = ax.legend(
        handles=legend_handles,
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.96),
        bbox_transform=ax.transAxes,
        borderaxespad=0.0,
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        ncol=len(legend_handles),
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
        framealpha=1.0,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("predictor_mpki", "ifuse_mpki"):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot simpoint-weighted fusion predictor accuracy (%) and MPKI."
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root simulations directory (default: %(default)s)",
    )
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--ifuse-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--helios-config", default=DEFAULT_HELIOS_CONFIG)
    parser.add_argument("--rfp-config", default=DEFAULT_RFP_CONFIG)
    parser.add_argument("--ifuse-config", default=DEFAULT_IPC_IFUSE_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot output directory (default: scarab/src/hpca2027-main-graphs-results/ifuse_accuracy)",
    )
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    sim_root = args.simulations_root
    helios_dir = args.helios_dir or DEFAULT_HELIOS_DIR
    rfp_dir = args.rfp_dir or DEFAULT_RFP_DIR
    ifuse_dir = args.ifuse_dir or DEFAULT_IPC_IFUSE_DIR
    workloads = [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    output_dir = args.output_dir or DEFAULT_IFUSE_ACCURACY_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Computing fusion predictor accuracy...")
    print(f"  helios: {helios_dir} (config={args.helios_config})")
    print(f"  rfp:    {rfp_dir} (config={args.rfp_config})")
    print(f"  ifuse:  {ifuse_dir} (config={args.ifuse_config})")
    print(f"  output: {output_dir}")

    results: list[AccuracyResult] = []
    helios_present = 0
    rfp_present = 0
    for workload in workloads:
        result = compute_workload_accuracy(
            workload,
            helios_dir,
            rfp_dir,
            ifuse_dir,
            sp_weights,
            helios_config=args.helios_config,
            rfp_config=args.rfp_config,
            ifuse_config=args.ifuse_config,
            suite=DEFAULT_SUITE,
            subsuite=DEFAULT_SUBSUITE,
        )
        if result is None:
            print(f"  skip {workload}: missing I-Fuse stats")
            continue
        results.append(result)
        if result.helios is not None:
            helios_present += 1
        if result.rfp is not None:
            rfp_present += 1
        parts = [f"  {workload:14s}"]
        if result.helios is not None:
            parts.append(f"helios={result.helios.accuracy_pct:7.4f}%/{result.helios.mpki:.4f}")
        if result.rfp is not None:
            parts.append(f"rfp={result.rfp.accuracy_pct:7.4f}%/{result.rfp.mpki:.4f}")
        parts.append(
            f"ifuse={result.ifuse.accuracy_pct:7.4f}%/{result.ifuse.mpki:.4f}  "
            f"(simpoints={result.ifuse.trace_count})"
        )
        print("  ".join(parts))

    if not results:
        raise SystemExit("No workloads with complete I-Fuse accuracy data.")

    include_helios = helios_present > 0
    include_rfp = rfp_present > 0

    write_summary_csv(
        output_dir / "predictor_accuracy_summary.csv",
        results,
        include_helios=include_helios,
        include_rfp=include_rfp,
    )
    write_computation_log(
        output_dir / "predictor_accuracy_computation_log.txt",
        results,
        include_helios=include_helios,
        include_rfp=include_rfp,
    )
    plot_accuracy_bars(
        results, output_dir, include_helios=include_helios, include_rfp=include_rfp
    )
    plot_mpki_bars(results, output_dir, include_helios=include_helios, include_rfp=include_rfp)

    def _mean(attr: str, key: str) -> float | None:
        vals = [
            getattr(getattr(r, key), attr)
            for r in results
            if getattr(r, key) is not None
        ]
        return sum(vals) / len(vals) if vals else None

    print("\nSummary:")
    print(f"  workloads plotted: {len(results)}")
    if include_helios:
        print(f"  helios accuracy mean: {_mean('accuracy_pct', 'helios'):.4f}%")
        print(f"  helios MPKI mean:       {_mean('mpki', 'helios'):.6f}")
    if include_rfp:
        print(f"  rfp accuracy mean:      {_mean('accuracy_pct', 'rfp'):.4f}%")
        print(f"  rfp MPKI mean:          {_mean('mpki', 'rfp'):.6f}")
    print(f"  ifuse accuracy mean:     {_mean('accuracy_pct', 'ifuse'):.4f}%")
    print(f"  ifuse MPKI mean:         {_mean('mpki', 'ifuse'):.6f}")
    print("\nOutputs:")
    for stem in ("predictor_accuracy", "predictor_mpki"):
        print(f"  - {output_dir / f'{stem}.png'}")
        print(f"  - {output_dir / f'{stem}.pdf'}")
        print(f"  - {output_dir / f'{stem}.eps'}")
    print(f"  - {output_dir / 'predictor_accuracy_summary.csv'}")
    print(f"  - {output_dir / 'predictor_accuracy_computation_log.txt'}")


if __name__ == "__main__":
    main()
