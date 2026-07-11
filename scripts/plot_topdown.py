#!/usr/bin/env python3
"""Plot Scarab TopDown breakdown into four categories with simpoint weighting.

Uses simpoint weights from collected_stats.csv (Weight row) to compute per-workload
weighted averages, then normalizes the four TopDown bound counters to fractions.

Example:
  python3 scripts/plot_topdown.py -d json/baseline.json
  python3 scripts/plot_topdown.py --csv /path/to/collected_stats.csv -c baseline
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Stat names for collection (any order)
TOPDOWN_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("TOPDOWN_RETIRING_BOUND", "Retiring"),
    ("TOPDOWN_FRONTEND_BOUND", "Frontend Bound"),
    ("TOPDOWN_BACKEND_BOUND", "Backend Bound"),
    ("TOPDOWN_BAD_SPEC_BOUND", "Bad Speculation"),
)

# Stacked bar order: bottom → top (Backend Bound at bottom, Retiring on top)
TOPDOWN_STACK_ORDER: Tuple[str, ...] = (
    "Backend Bound",
    "Frontend Bound",
    "Bad Speculation",
    "Retiring",
)

TOPDOWN_COLORS = {
    "Retiring": "#4daf4a",
    "Frontend Bound": "#377eb8",
    "Backend Bound": "#ff7f00",
    "Bad Speculation": "#984ea3",
}

# Plot styling (aligned with instruction-fusion plot_ipc.py)
CATEGORY_GAP = 0.80
AXIS_FONT = 20
LEGEND_FONT = 15
FIGSIZE = (19.0, 11.3)
BAR_WIDTH = 0.35
SUMMARY_COLUMN_SHADE_FACE = "#c0c0c0"
SUMMARY_COLUMN_SHADE_ALPHA = 0.28
SUMMARY_SEPARATOR_COLOR = "#DC3B23"
SUMMARY_XTICK = "Average"

DISPLAY_APP_NAMES = {
    "bc": "betweenness centrality",
    "bfs": "breadth first search",
    "cc": "connected components",
    "cd": "community detection",
    "dfs": "depth first search",
    "pagerank": "pagerank",
    "sssp_ego_fb": "single source shortest path",
    "tc": "triangle counting",
    "langchain_web": "langchain",
    "rag_haystack": "rag haystack",
    "swe_agent": "swe agent",
    "chemcrow": "chemcrow",
    "toolformer": "toolformer",
}


def workload_app_name(workload: str) -> str:
    """Extract application name from suite/subsuite/workload path."""
    parts = workload.strip("/").split("/")
    return parts[-1] if parts else workload


def display_app_name(workload: str) -> str:
    app = workload_app_name(workload)
    return DISPLAY_APP_NAMES.get(app, app)


def normalize_output_base(path: str | Path) -> str:
    p = Path(path)
    if p.suffix.lower() in (".png", ".pdf"):
        p = p.with_suffix("")
    return str(p)


def _shade_summary_column(ax, n_categories: int) -> None:
    if n_categories <= 1:
        return
    last = (n_categories - 1) * CATEGORY_GAP
    ax.axvspan(
        last - 0.5 * CATEGORY_GAP,
        last + 0.55 * CATEGORY_GAP,
        facecolor=SUMMARY_COLUMN_SHADE_FACE,
        alpha=SUMMARY_COLUMN_SHADE_ALPHA,
        zorder=-1,
        linewidth=0,
        clip_on=True,
    )


def _tight_x_limits(ax, n_categories: int) -> None:
    if n_categories < 1:
        return
    last = (n_categories - 1) * CATEGORY_GAP
    left_pad = 0.085
    right_pad = 0.048
    xmin = -BAR_WIDTH - left_pad
    xmax = last + BAR_WIDTH + right_pad
    ax.set_xlim(xmin, xmax)
    ax.margins(x=0)

def _ensure_import_paths() -> None:
    here = Path(__file__).resolve().parent
    root = here.parent
    stats_dir = root / "scarab_stats"
    for path in (here, root, stats_dir):
        sp = str(path)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def _load_descriptor(descriptor_path: Path) -> dict:
    with descriptor_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _stats_path_from_descriptor(descriptor: dict) -> Path:
    root_dir = Path(descriptor["root_dir"])
    experiment = descriptor["experiment"]
    return root_dir / "simulations" / experiment / "collected_stats.csv"


def _resolve_stat_name(available: set[str], base_name: str) -> Optional[str]:
    count_name = f"{base_name}_count"
    if count_name in available:
        return count_name
    if base_name in available:
        return base_name
    return None


def _resolve_topdown_stats(available_stats: Sequence[str]) -> List[Tuple[str, str]]:
    available = set(available_stats)
    resolved: List[Tuple[str, str]] = []
    missing: List[str] = []
    for base_name, label in TOPDOWN_CATEGORIES:
        stat = _resolve_stat_name(available, base_name)
        if stat is None:
            missing.append(base_name)
        else:
            resolved.append((stat, label))
    if missing:
        raise ValueError(
            "TopDown stats missing from collected stats: "
            + ", ".join(missing)
            + ". Re-collect stats with core.stat.0.csv present."
        )
    return resolved


def _weighted_category_values(
    experiment,
    config: str,
    workloads: Sequence[str],
    resolved_stats: Sequence[Tuple[str, str]],
) -> Dict[str, Dict[str, float]]:
    """Return raw simpoint-weighted counts per workload and category label."""
    stat_keys = [stat for stat, _ in resolved_stats]
    all_data = experiment.retrieve_stats([config], stat_keys, list(workloads))
    if all_data is None:
        raise RuntimeError(f"Failed to retrieve TopDown stats for config '{config}'")

    by_workload: Dict[str, Dict[str, float]] = {}
    for workload in workloads:
        by_workload[workload] = {}
        for stat, label in resolved_stats:
            key = f"{config} {workload} {stat}"
            if key not in all_data:
                raise KeyError(f"Missing stat column for {key}")
            by_workload[workload][label] = float(all_data[key])
    return by_workload


def _to_fractions(raw: Dict[str, float]) -> Dict[str, float]:
    total = sum(raw.values())
    if total <= 0:
        return {label: float("nan") for label in raw}
    return {label: value / total for label, value in raw.items()}


def _category_average(
    fractions_by_workload: Dict[str, Dict[str, float]], label: str
) -> float:
    vals = [fracs[label] for fracs in fractions_by_workload.values()]
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)


def print_topdown_table(
    fractions_by_workload: Dict[str, Dict[str, float]],
    *,
    include_average: bool = True,
) -> None:
    labels = list(reversed(TOPDOWN_STACK_ORDER))
    rows = list(fractions_by_workload.items())
    if include_average:
        average_row = {
            label: _category_average(fractions_by_workload, label) for label in labels
        }
        rows.append((SUMMARY_XTICK, average_row))

    header = ["Workload"] + labels
    print("| " + " | ".join(header) + " |")
    print("| " + " | ".join(["---"] * len(header)) + " |")
    for workload, fracs in rows:
        cells = [display_app_name(workload) if workload != SUMMARY_XTICK else SUMMARY_XTICK]
        for label in labels:
            val = fracs.get(label, float("nan"))
            cells.append("N/A" if math.isnan(val) else f"{val * 100:.1f}%")
        print("| " + " | ".join(cells) + " |")


def plot_topdown_stacked(
    fractions_by_workload: Dict[str, Dict[str, float]],
    *,
    config: str,
    output_stem: str,
    title: str,
    include_average: bool = True,
) -> None:
    import matplotlib.pyplot as plt
    import numpy as np

    labels = list(TOPDOWN_STACK_ORDER)
    workload_keys = list(fractions_by_workload.keys())
    display_apps = [display_app_name(w) for w in workload_keys]

    average_row: Dict[str, float] = {}
    if include_average:
        for label in labels:
            average_row[label] = _category_average(fractions_by_workload, label)
        display_apps = display_apps + [SUMMARY_XTICK]

    n = len(display_apps)
    x = [i * CATEGORY_GAP for i in range(n)]
    bottoms = np.zeros(n)

    plt.rcParams.update({"font.size": 15, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=FIGSIZE)

    if include_average and n > 1:
        _shade_summary_column(ax, n)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for label in labels:
        heights = []
        for idx, _display in enumerate(display_apps):
            if include_average and idx == n - 1:
                val = average_row[label]
            else:
                val = fractions_by_workload[workload_keys[idx]][label]
            heights.append(0.0 if math.isnan(val) else val)
        ax.bar(
            x,
            heights,
            BAR_WIDTH,
            bottom=bottoms,
            label=label,
            color=TOPDOWN_COLORS[label],
            edgecolor="black",
            linewidth=1.5,
            zorder=3,
        )
        bottoms += np.array(heights)

    if include_average and n > 1:
        ax.axvline(
            x=(n - 1.5) * CATEGORY_GAP,
            color=SUMMARY_SEPARATOR_COLOR,
            linestyle="--",
            alpha=0.8,
            linewidth=3.0,
            zorder=2,
        )

    ax.set_ylim(0, 1.0)
    ax.set_ylabel(
        "TopDown breakdown (%)",
        fontsize=AXIS_FONT,
        fontfamily="serif",
        labelpad=14,
    )
    if title:
        ax.set_title(title, fontsize=AXIS_FONT, fontfamily="serif", pad=16)
    ax.set_xticks(x)
    ax.set_xticklabels(
        display_apps,
        rotation=45,
        ha="right",
        fontsize=AXIS_FONT,
        fontfamily="serif",
    )
    if include_average:
        for tick in ax.get_xticklabels():
            if tick.get_text() == SUMMARY_XTICK:
                tick.set_weight("bold")

    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}"))
    ax.tick_params(axis="y", labelsize=AXIS_FONT)
    for tick in ax.get_yticklabels():
        tick.set_fontfamily("serif")
    _tight_x_limits(ax, n)

    leg = ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        fontsize=LEGEND_FONT,
        frameon=True,
        fancybox=False,
        edgecolor="black",
        ncol=4,
        columnspacing=0.9,
        handletextpad=0.4,
        borderpad=0.35,
        handlelength=1.1,
        handleheight=0.58,
    )
    handles, leg_labels = ax.get_legend_handles_labels()
    leg.remove()
    leg = ax.legend(
        handles[::-1],
        leg_labels[::-1],
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        fontsize=LEGEND_FONT,
        frameon=True,
        fancybox=False,
        edgecolor="black",
        ncol=4,
        columnspacing=0.9,
        handletextpad=0.4,
        borderpad=0.35,
        handlelength=1.1,
        handleheight=0.58,
    )
    leg.get_frame().set_linewidth(2.5)
    leg.get_frame().set_facecolor("white")
    leg.get_frame().set_alpha(0.95)
    for text in leg.get_texts():
        text.set_fontsize(LEGEND_FONT)
        text.set_fontfamily("serif")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout(pad=0.25, rect=[0.0, 0.0, 1.0, 0.90])
    out_dir = Path(output_stem).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    png = f"{output_stem}.png"
    pdf = f"{output_stem}.pdf"
    fig.savefig(png, bbox_inches="tight", dpi=300, pad_inches=0.06)
    fig.savefig(pdf, bbox_inches="tight", dpi=300, pad_inches=0.06)
    plt.close(fig)
    print(f"Saved {png} and {pdf}")


def _select_workloads(experiment, requested: Optional[List[str]]) -> List[str]:
    available = sorted(experiment.get_workloads())
    if not requested:
        return available
    missing = sorted(set(requested) - set(available))
    if missing:
        raise ValueError(f"Workloads not found in stats file: {', '.join(missing)}")
    return requested


def _select_configs(experiment, requested: Optional[List[str]]) -> List[str]:
    available = sorted(experiment.get_configurations())
    if not requested:
        return available
    missing = sorted(set(requested) - set(available))
    if missing:
        raise ValueError(f"Configs not found in stats file: {', '.join(missing)}")
    return requested


def main() -> int:
    _ensure_import_paths()
    import scarab_stats  # noqa: E402

    parser = argparse.ArgumentParser(
        description="Plot TopDown 4-category breakdown with simpoint weighting."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("-d", "--descriptor", type=Path, help="Experiment descriptor JSON")
    src.add_argument("--csv", type=Path, help="Path to collected_stats.csv")
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        dest="configs",
        help="Configuration to plot (repeatable; default: all configs in stats file)",
    )
    parser.add_argument(
        "-w",
        "--workload",
        action="append",
        dest="workloads",
        help="Workload to plot (repeatable; default: all workloads in stats file)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output base path without extension (writes .png and .pdf)",
    )
    parser.add_argument(
        "--no-average",
        action="store_true",
        help="Do not add an Average summary bar to the plot/table",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional plot title",
    )
    args = parser.parse_args()

    if args.descriptor:
        descriptor = _load_descriptor(args.descriptor)
        stats_path = _stats_path_from_descriptor(descriptor)
        default_output = normalize_output_base(
            stats_path.with_name("topdown_4category_stacked")
        )
    else:
        stats_path = args.csv
        default_output = normalize_output_base(
            stats_path.with_name("topdown_4category_stacked")
        )

    if not stats_path.is_file():
        print(f"Stats file not found: {stats_path}", file=sys.stderr)
        print("Run: ./sci --collect-stats <descriptor>", file=sys.stderr)
        return 1

    aggregator = scarab_stats.stat_aggregator()
    experiment = aggregator.load_experiment_csv(str(stats_path))
    resolved_stats = _resolve_topdown_stats(experiment.get_stats())

    workloads = _select_workloads(experiment, args.workloads)
    configs = _select_configs(experiment, args.configs)
    include_average = not args.no_average

    print(f"Using stats file: {stats_path}")
    print("TopDown categories (simpoint-weighted):")
    for stat, label in resolved_stats:
        print(f"  {label}: {stat}")

    output_stem = normalize_output_base(args.output or default_output)
    if len(configs) == 1:
        config = configs[0]
        raw = _weighted_category_values(experiment, config, workloads, resolved_stats)
        fractions = {wl: _to_fractions(vals) for wl, vals in raw.items()}
        print()
        print_topdown_table(fractions, include_average=include_average)
        print()
        plot_topdown_stacked(
            fractions,
            config=config,
            output_stem=output_stem,
            title=args.title,
            include_average=include_average,
        )
        return 0

    # Multiple configs: one plot per config
    for config in configs:
        raw = _weighted_category_values(experiment, config, workloads, resolved_stats)
        fractions = {wl: _to_fractions(vals) for wl, vals in raw.items()}
        config_stem = f"{output_stem}_{config}"
        print()
        print(f"=== {config} ===")
        print_topdown_table(fractions, include_average=include_average)
        plot_topdown_stacked(
            fractions,
            config=config,
            output_stem=config_stem,
            title=args.title or f"TopDown breakdown ({config})",
            include_average=include_average,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
