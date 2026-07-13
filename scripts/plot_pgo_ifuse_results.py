#!/usr/bin/env python3
"""Plot PGO I-Fuse experiment results normalized to baseline.

Reads scarab-infra collected_stats.csv plus PGO candidate directories to produce:
  1. Pareto curve: simpoint-weighted FCT storage vs IPC speedup
  2. Per-workload IPC speedup bar chart (baseline = 1.0)
  3. Aggregate IPC speedup vs PGO frequency threshold
  4. Summary CSV with speedup and storage per configuration

Example:
  /users/deepmish/miniconda3/envs/scarabinfra/bin/python \\
    scripts/plot_pgo_ifuse_results.py \\
    --stats-csv /users/deepmish/scarab/src/simulations/pgo_ifuse_frequency_sweep/collected_stats.csv \\
    --output-dir /users/deepmish/scarab/src/simulations/pgo_ifuse_frequency_sweep/plots
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKLOADS_DB = REPO_ROOT / "workloads" / "workloads_db.json"
DEFAULT_BITS_PER_ENTRY = 108

PGO_CONFIGS = [
    "pgo_freq_1",
    "pgo_freq_10",
    "pgo_freq_100",
    "pgo_freq_1000",
    "pgo_freq_10000",
    "pgo_freq_100000",
]

CONFIG_TO_FREQ = {
    "pgo_freq_1": 1,
    "pgo_freq_10": 10,
    "pgo_freq_100": 100,
    "pgo_freq_1000": 1000,
    "pgo_freq_10000": 10000,
    "pgo_freq_100000": 100000,
}

DEFAULT_IFUSE_STATS = [
    "IFUSE_FUSED_LOADS",
    "IFUSE_LOAD1_PREDICTIONS",
    "IFUSE_CORRECT_PREDICTIONS",
    "IFUSE_INCORRECT_PREDICTIONS",
    "IFUSE_MISPREDICTED_LOADS",
    "APT_LIVE_LD2_PREDICTION_PEAK",
]

# Micro2026-style plot aesthetics (aligned with instruction-fusion plot_ipc.py).
AVERAGE_COLUMN_SHADE_FACE = "#c0c0c0"
AVERAGE_COLUMN_SHADE_ALPHA = 0.28
AVERAGE_SEP_COLOR = "#DC3B23"
IPC_CATEGORY_GAP = 0.78
IPC_AXIS_FONT = 34
IPC_LEGEND_FONT = 30
IPC_FIGSIZE = (26.0, 14.5)
IPC_BAR_LABEL_FONT = 15
IPC_Y_AXIS_TOP_TICK = 20.0
IPC_Y_AXIS_HEADROOM = 3.0
IPC_YLIM_DEFAULT = (0.0, IPC_Y_AXIS_TOP_TICK + IPC_Y_AXIS_HEADROOM)
_BAR_ZERO_EPS = 1e-6
PGO_IFUSE_COLOR = "#00FF00"
# Darker greens for higher frequency thresholds (same technique, tighter filter).
PGO_FREQ_COLORS = [
    "#c8f7c5",
    "#7ae582",
    PGO_IFUSE_COLOR,
    "#00cc00",
    "#009900",
    "#006600",
]


def rename_workload(workload: str) -> str:
    mapping = {
        "bfs": "breadth first search",
        "dfs": "depth first search",
        "pagerank": "pagerank",
        "tc": "triangle counting",
        "cc": "connected components",
        "cd": "community detection",
        "bc": "betweenness centrality",
        "sssp_ego_fb": "single source shortest path",
    }
    return mapping.get(workload, workload)


def _bar_edge_styles(values: list[float]) -> tuple[list[str], list[float]]:
    edges: list[str] = []
    lws: list[float] = []
    for val in values:
        if abs(val) <= _BAR_ZERO_EPS:
            edges.append("none")
            lws.append(0.0)
        else:
            edges.append("black")
            lws.append(1.5)
    return edges, lws


def _hide_zero_value_bars(container, values: list[float]) -> None:
    for patch, val in zip(container.patches, values, strict=True):
        if abs(val) <= _BAR_ZERO_EPS:
            patch.set_visible(False)
            patch.set_linewidth(0.0)
            patch.set_edgecolor("none")


def _shade_summary_column(ax, n_x_categories: int, *, category_gap: float) -> None:
    if n_x_categories <= 1:
        return
    ax.axvspan(
        (n_x_categories - 1.5) * category_gap,
        (n_x_categories - 0.45) * category_gap,
        facecolor=AVERAGE_COLUMN_SHADE_FACE,
        alpha=AVERAGE_COLUMN_SHADE_ALPHA,
        zorder=-1,
        linewidth=0,
        clip_on=True,
    )


def _tight_ipc_x_limits(ax, n_categories: int, *, category_gap: float) -> None:
    if n_categories < 1:
        return
    last = (n_categories - 1) * category_gap
    w = 0.12
    right_pad = 0.055
    left_pad = 0.095
    ax.set_xlim(-2 * w - w / 2 - left_pad, last + 2 * w + w / 2 + right_pad)
    ax.margins(x=0)


def _apply_speedup_y_ticks(ax) -> None:
    import matplotlib.ticker as mticker

    ax.yaxis.set_major_locator(mticker.MultipleLocator(5))


def _ylim_speedup_pct_auto(*series: list[float], pad_frac: float = 0.06) -> tuple[float, float]:
    vals = [x for s in series for x in s if not math.isnan(x)]
    if not vals:
        return IPC_YLIM_DEFAULT
    lo = min(vals)
    hi = max(vals)
    span = hi - lo
    pad = pad_frac * span if span > 1e-9 else max(abs(hi) * pad_frac, 0.5)
    ymin = 0.0 if lo >= 0 else lo - pad
    ymax = hi + pad
    if ymax <= ymin:
        ymax = ymin + 1.0
    return (ymin, ymax)


def _ylim_with_bar_label_headroom(
    ylim: tuple[float, float], *, headroom_frac: float = 0.14
) -> tuple[float, float]:
    ymin, ymax = ylim
    span = ymax - ymin
    if span <= 0:
        span = 1.0
    return (ymin, ymax + span * headroom_frac)


def _annotate_bar_speedup_labels(
    ax, container, values: list[float], *, fontsize: int = IPC_BAR_LABEL_FONT
) -> None:
    for patch, val in zip(container.patches, values, strict=True):
        if abs(val) <= _BAR_ZERO_EPS or math.isnan(val):
            continue
        height = patch.get_height()
        x = patch.get_x() + patch.get_width() / 2.0
        ax.text(
            x,
            height,
            f"{val:+.1f}",
            ha="center",
            va="bottom",
            fontsize=fontsize,
            fontfamily="serif",
            rotation=90,
            zorder=4,
        )


def _apply_micro2026_rcparams() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.size": 15,
            "font.family": "serif",
            "axes.labelsize": IPC_AXIS_FONT,
            "xtick.labelsize": IPC_AXIS_FONT,
            "ytick.labelsize": IPC_AXIS_FONT,
            "legend.fontsize": IPC_LEGEND_FONT,
        }
    )


def _style_axes_and_legend(ax, legend) -> None:
    import matplotlib.pyplot as plt

    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_color("black")
        sp.set_linewidth(2.5)
    legend.get_frame().set_linewidth(2.5)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(0.95)
    for t in legend.get_texts():
        t.set_fontsize(IPC_LEGEND_FONT)
        t.set_fontfamily("serif")
    ax.yaxis.label.set_fontsize(IPC_AXIS_FONT)
    ax.yaxis.label.set_fontfamily("serif")
    for t in ax.get_xticklabels():
        t.set_fontsize(IPC_AXIS_FONT)
        t.set_fontfamily("serif")
    for t in ax.get_yticklabels():
        t.set_fontsize(IPC_AXIS_FONT)
        t.set_fontfamily("serif")


def _save_figure(fig, path: Path) -> None:
    stem = path.with_suffix("")
    fig.savefig(f"{stem}.png", bbox_inches="tight", dpi=300, pad_inches=0.06)
    fig.savefig(f"{stem}.pdf", bbox_inches="tight", dpi=300, pad_inches=0.06)


@dataclass(frozen=True)
class SimpointKey:
    config: str
    workload: str
    cluster_id: str


def parse_column_name(column: str) -> SimpointKey | None:
    """Parse '{config} {suite}/{subsuite}/{workload} {cluster_id}'."""
    column = column.strip()
    if not column or column in {"stats", "write_protect", "groups"}:
        return None
    parts = column.rsplit(" ", 1)
    if len(parts) != 2:
        return None
    prefix, cluster_id = parts
    config, workload_path = prefix.split(" ", 1)
    workload = workload_path.rsplit("/", 1)[-1]
    return SimpointKey(config=config, workload=workload, cluster_id=cluster_id)


def bits_to_kib(entries: float, bits_per_entry: int = DEFAULT_BITS_PER_ENTRY) -> float:
    return entries * bits_per_entry / 8.0 / 1024.0


def count_fct_entries(csv_path: Path) -> int:
    seen_ld1: set[str] = set()
    with csv_path.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if not row or not row[0].strip():
                continue
            ld1_pc = row[0].strip()
            if ld1_pc not in seen_ld1:
                seen_ld1.add(ld1_pc)
    return len(seen_ld1)


def load_stats_table(stats_csv: Path) -> tuple[dict[str, dict[SimpointKey, float]], list[SimpointKey]]:
    """Return ({stat_name: {simpoint: value}}, ordered simpoint keys)."""
    with stats_csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise SystemExit(f"empty stats csv: {stats_csv}")

        columns = [c for c in reader.fieldnames if c not in {"stats", "write_protect", "groups"}]
        keys = [parse_column_name(c) for c in columns]
        simpoints = [k for k in keys if k is not None]

        table: dict[str, dict[SimpointKey, float]] = {}
        for row in reader:
            stat = row.get("stats", "").strip()
            if not stat:
                continue
            values: dict[SimpointKey, float] = {}
            for col, key in zip(columns, keys):
                if key is None:
                    continue
                raw = row.get(col, "")
                if raw in ("", "nan", "NaN", None):
                    continue
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if math.isnan(val):
                    continue
                values[key] = val
            table[stat] = values
    return table, simpoints


def weighted_mean(
    values: dict[SimpointKey, float],
    weights: dict[SimpointKey, float],
    *,
    config: str | None = None,
    workload: str | None = None,
) -> float:
    total = 0.0
    weight_sum = 0.0
    for key, weight in weights.items():
        if config is not None and key.config != config:
            continue
        if workload is not None and key.workload != workload:
            continue
        if key not in values:
            continue
        total += values[key] * weight
        weight_sum += weight
    return total / weight_sum if weight_sum else float("nan")


def apply_external_baseline_ipc(
    table: dict[str, dict[SimpointKey, float]],
    external_stats_csv: Path,
    *,
    source_config: str = "baseline",
    target_config: str = "baseline",
) -> int:
    """Overlay IPC from an external collected_stats.csv onto baseline keys."""
    ext_table, _ = load_stats_table(external_stats_csv)
    ext_ipc = ext_table.get("IPC")
    if not ext_ipc:
        raise SystemExit(f"IPC row missing from external baseline stats: {external_stats_csv}")

    ipc = table.setdefault("IPC", {})
    replaced = 0
    for key, val in ext_ipc.items():
        if key.config != source_config:
            continue
        ipc[SimpointKey(target_config, key.workload, key.cluster_id)] = val
        replaced += 1
    return replaced


def build_weights(simpoints: Iterable[SimpointKey], weight_row: dict[SimpointKey, float]) -> dict[SimpointKey, float]:
    weights: dict[SimpointKey, float] = {}
    for key in simpoints:
        if key.config != "baseline":
            continue
        w = weight_row.get(key)
        if w is None or math.isnan(w) or w <= 0:
            continue
        # Use baseline simpoint weights for all configs (same simpoints).
        weights[SimpointKey("baseline", key.workload, key.cluster_id)] = w
        for cfg in PGO_CONFIGS:
            weights[SimpointKey(cfg, key.workload, key.cluster_id)] = w
    return weights


def expected_storage_kib(
    config: str,
    pgo_root: Path,
    weights: dict[SimpointKey, float],
    bits_per_entry: int,
) -> tuple[float, float]:
    freq = CONFIG_TO_FREQ.get(config)
    if freq is None:
        return float("nan"), float("nan")
    freq_dir = pgo_root / f"pgo-candidates-frequency-{freq}"
    total_kib = 0.0
    total_entries = 0.0
    weight_sum = 0.0
    for key, weight in weights.items():
        if key.config != "baseline":
            continue
        csv_path = freq_dir / key.workload / f"{key.cluster_id}.csv"
        if not csv_path.is_file():
            continue
        entries = count_fct_entries(csv_path)
        total_kib += weight * bits_to_kib(entries, bits_per_entry)
        total_entries += weight * entries
        weight_sum += weight
    if weight_sum == 0:
        return float("nan"), float("nan")
    return total_kib / weight_sum, total_entries / weight_sum


def summarize_configs(
    table: dict[str, dict[SimpointKey, float]],
    weights: dict[SimpointKey, float],
    baseline_config: str,
    pgo_root: Path,
    bits_per_entry: int,
    workloads: list[str],
) -> list[dict]:
    ipc = table.get("IPC", {})
    baseline_ipc = weighted_mean(ipc, weights, config=baseline_config)
    rows: list[dict] = []

    for config in PGO_CONFIGS:
        cfg_ipc = weighted_mean(ipc, weights, config=config)
        speedup = cfg_ipc / baseline_ipc if baseline_ipc and not math.isnan(cfg_ipc) else float("nan")
        speedup_pct = (speedup - 1.0) * 100.0 if not math.isnan(speedup) else float("nan")
        storage_kib, expected_entries = expected_storage_kib(
            config, pgo_root, weights, bits_per_entry
        )
        row = {
            "config": config,
            "frequency": CONFIG_TO_FREQ[config],
            "weighted_ipc": cfg_ipc,
            "baseline_ipc": baseline_ipc,
            "ipc_speedup": speedup,
            "ipc_speedup_pct": speedup_pct,
            "weighted_storage_kib": storage_kib,
            "weighted_fct_entries": expected_entries,
        }
        for wl in workloads:
            wl_base = weighted_mean(ipc, weights, config=baseline_config, workload=wl)
            wl_cfg = weighted_mean(ipc, weights, config=config, workload=wl)
            row[f"{wl}_speedup"] = (
                wl_cfg / wl_base if wl_base and not math.isnan(wl_cfg) else float("nan")
            )
        rows.append(row)

    rows.sort(key=lambda r: r["weighted_storage_kib"])
    return rows


def write_summary_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def plot_results(
    rows: list[dict],
    table: dict[str, dict[SimpointKey, float]],
    weights: dict[SimpointKey, float],
    baseline_config: str,
    workloads: list[str],
    output_dir: Path,
    extra_stats: list[str],
) -> None:
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    _apply_micro2026_rcparams()

    # --- Plot 1: Pareto (storage vs IPC speedup %) ---
    fig, ax = plt.subplots(figsize=(12.0, 7.5))
    xs = [r["weighted_storage_kib"] for r in rows]
    ys = [r["ipc_speedup_pct"] for r in rows]
    labels = [str(r["frequency"]) for r in rows]
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    ax.plot(xs, ys, marker="o", linewidth=2.0, markersize=8, color=PGO_IFUSE_COLOR)
    for x, y, label in zip(xs, ys, labels):
        ax.annotate(
            f"N={label}",
            (x, y),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=14,
            fontfamily="serif",
        )
    ax.axhline(0.0, color="#888888", linewidth=1.0, linestyle="--")
    ax.set_xlabel("Expected FCT storage (KiB, simpoint-weighted)", fontfamily="serif")
    ax.set_ylabel("Weighted IPC speedup vs baseline (%)", fontfamily="serif")
    ax.set_title("PGO I-Fuse: storage vs IPC trade-off", fontfamily="serif")
    for sp in ax.spines.values():
        sp.set_linewidth(2.5)
        sp.set_color("black")
    fig.tight_layout()
    _save_figure(fig, output_dir / "pareto_storage_ipc.png")
    plt.close(fig)

    # --- Plot 2: IPC speedup vs frequency (aggregate) ---
    fig, ax = plt.subplots(figsize=(12.0, 7.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
    freqs = [r["frequency"] for r in sorted(rows, key=lambda r: r["frequency"])]
    speedup_pct = [
        next(r["ipc_speedup_pct"] for r in rows if r["frequency"] == f)
        for f in freqs
    ]
    ax.plot(freqs, speedup_pct, marker="o", linewidth=2.0, markersize=8, color=PGO_IFUSE_COLOR)
    ax.axhline(0.0, color="#888888", linewidth=1.0, linestyle="--", label="baseline")
    ax.set_xscale("log")
    ax.set_xlabel("PGO occurrence threshold (N)", fontfamily="serif")
    ax.set_ylabel("Weighted IPC speedup vs baseline (%)", fontfamily="serif")
    ax.set_title("Aggregate IPC speedup vs PGO frequency", fontfamily="serif")
    leg = ax.legend(frameon=True, fancybox=False, edgecolor="black")
    _style_axes_and_legend(ax, leg)
    fig.tight_layout()
    _save_figure(fig, output_dir / "ipc_speedup_vs_frequency.png")
    plt.close(fig)

    # --- Plot 3: Per-workload IPC speedup bars (Micro2026 grouped-bar style) ---
    configs_sorted = sorted(PGO_CONFIGS, key=lambda c: CONFIG_TO_FREQ[c])
    ipc = table.get("IPC", {})
    norms_by_config: dict[str, list[float]] = {}
    for config in configs_sorted:
        norms: list[float] = []
        for wl in workloads:
            base = weighted_mean(ipc, weights, config=baseline_config, workload=wl)
            cur = weighted_mean(ipc, weights, config=config, workload=wl)
            norms.append(cur / base if base and not math.isnan(cur) else float("nan"))
        norms_by_config[config] = norms

    display_workloads = [rename_workload(wl) for wl in workloads]
    pct_by_config = {
        cfg: [(v - 1.0) * 100.0 if not math.isnan(v) else float("nan") for v in norms]
        for cfg, norms in norms_by_config.items()
    }
    mean_norms = [
        sum(norms_by_config[cfg]) / len(workloads) for cfg in configs_sorted
    ]
    mean_pct = [(v - 1.0) * 100.0 for v in mean_norms]

    display_apps = display_workloads + ["Average"]
    g = IPC_CATEGORY_GAP
    x = [i * g for i in range(len(display_apps))]
    width = 0.12
    n_cfgs = len(configs_sorted)
    offsets = [(i - (n_cfgs - 1) / 2.0) * width for i in range(n_cfgs)]

    fig, ax = plt.subplots(figsize=IPC_FIGSIZE)
    _shade_summary_column(ax, len(display_apps), category_gap=g)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for idx, (offset, config) in enumerate(zip(offsets, configs_sorted)):
        freq = CONFIG_TO_FREQ[config]
        vals_w = pct_by_config[config]
        vals = vals_w + [mean_pct[idx]]
        color = PGO_FREQ_COLORS[idx] if idx < len(PGO_FREQ_COLORS) else PGO_IFUSE_COLOR
        edge, lw = _bar_edge_styles(vals)
        container = ax.bar(
            [xi + offset for xi in x],
            vals,
            width,
            label=f"N={freq}",
            alpha=1.0,
            color=color,
            edgecolor=edge,
            linewidth=lw,
            zorder=3,
        )
        _hide_zero_value_bars(container, vals)
        _annotate_bar_speedup_labels(ax, container, vals)

    ax.axvline(
        x=(len(display_apps) - 1.5) * g,
        color=AVERAGE_SEP_COLOR,
        linestyle="--",
        alpha=0.8,
        linewidth=3.5,
        zorder=2,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(
        display_apps,
        rotation=45,
        ha="right",
        fontsize=IPC_AXIS_FONT,
        fontfamily="serif",
    )
    for lab in ax.get_xticklabels():
        if lab.get_text() == "Average":
            lab.set_weight("bold")

    ylim = _ylim_with_bar_label_headroom(
        _ylim_speedup_pct_auto(
            *[pct_by_config[cfg] + [mean_pct[i]] for i, cfg in enumerate(configs_sorted)]
        )
    )
    ax.set_ylabel(
        "Speedup (%)\n(normalized to no-fusion\nbaseline)",
        fontsize=IPC_AXIS_FONT,
        fontfamily="serif",
        labelpad=14,
    )
    ax.set_ylim(ylim[0], ylim[1])
    ax.margins(x=0, y=0)
    _apply_speedup_y_ticks(ax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}"))
    _tight_ipc_x_limits(ax, len(display_apps), category_gap=g)

    leg = ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.04),
        fontsize=IPC_LEGEND_FONT,
        frameon=True,
        fancybox=False,
        edgecolor="black",
        ncol=3,
        columnspacing=0.85,
        handletextpad=0.4,
        labelspacing=0.28,
        borderpad=0.35,
        handlelength=1.15,
        handleheight=0.62,
    )
    _style_axes_and_legend(ax, leg)
    plt.tight_layout(pad=0.35, rect=[0.02, 0.02, 0.98, 0.86])
    _save_figure(fig, output_dir / "ipc_speedup_by_workload.png")
    plt.close(fig)

    # --- Plot 4: Optional I-Fuse stat ratios vs baseline ---
    for stat in extra_stats:
        if stat not in table or stat == "IPC":
            continue
        stat_values = table[stat]
        fig, ax = plt.subplots(figsize=(12.0, 7.5))
        ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)
        freqs = [r["frequency"] for r in sorted(rows, key=lambda r: r["frequency"])]
        base_val = weighted_mean(stat_values, weights, config=baseline_config)
        ratios = []
        for f in freqs:
            config = next(r["config"] for r in rows if r["frequency"] == f)
            cur = weighted_mean(stat_values, weights, config=config)
            if base_val and not math.isnan(cur) and base_val != 0:
                ratios.append(cur / base_val)
            else:
                ratios.append(float("nan"))
        ratio_pct = [(v - 1.0) * 100.0 if not math.isnan(v) else float("nan") for v in ratios]
        ax.plot(freqs, ratio_pct, marker="o", linewidth=2.0, markersize=8, color=PGO_IFUSE_COLOR)
        ax.axhline(0.0, color="#888888", linewidth=1.0, linestyle="--")
        ax.set_xscale("log")
        ax.set_xlabel("PGO occurrence threshold (N)", fontfamily="serif")
        ax.set_ylabel(f"{stat} change vs baseline (%)", fontfamily="serif")
        ax.set_title(f"{stat} normalized to baseline", fontfamily="serif")
        for sp in ax.spines.values():
            sp.set_linewidth(2.5)
            sp.set_color("black")
        fig.tight_layout()
        safe = stat.lower().replace(" ", "_")
        _save_figure(fig, output_dir / f"{safe}_vs_frequency.png")
        plt.close(fig)


def print_summary(rows: list[dict], baseline_config: str) -> None:
    print(f"\nPGO I-Fuse summary (normalized to {baseline_config}):")
    print(
        f"{'freq':>8}  {'speedup':>8}  {'speedup%':>9}  "
        f"{'storage_kib':>11}  {'entries':>8}"
    )
    for row in sorted(rows, key=lambda r: r["frequency"]):
        print(
            f"{row['frequency']:>8}  {row['ipc_speedup']:>8.4f}  "
            f"{row['ipc_speedup_pct']:>8.2f}%  "
            f"{row['weighted_storage_kib']:>11.3f}  "
            f"{row['weighted_fct_entries']:>8.1f}"
        )


def discover_workloads(simpoints: Iterable[SimpointKey]) -> list[str]:
    return sorted({k.workload for k in simpoints if k.config == "baseline"})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stats-csv",
        required=True,
        help="Path to collected_stats.csv from the experiment.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for PNG/CSV outputs (defaults next to stats csv).",
    )
    parser.add_argument(
        "--pgo-root",
        default="/users/deepmish/scarab/src/pgo-candidates",
        help="Root directory with pgo-candidates-frequency-<N>/ trees.",
    )
    parser.add_argument(
        "--baseline-stats-csv",
        default=None,
        help=(
            "Optional external collected_stats.csv for baseline IPC "
            "(e.g. ideal-fusion baseline experiment)."
        ),
    )
    parser.add_argument(
        "--baseline-config",
        default="baseline",
        help="Configuration name to normalize against.",
    )
    parser.add_argument(
        "--bits-per-entry",
        type=int,
        default=DEFAULT_BITS_PER_ENTRY,
        help="Hardware FCT bits per entry for storage estimates.",
    )
    parser.add_argument(
        "--extra-stats",
        nargs="*",
        default=DEFAULT_IFUSE_STATS,
        help="Additional stats to plot as ratios vs baseline.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stats_csv = Path(args.stats_csv)
    if not stats_csv.is_file():
        raise SystemExit(f"stats csv not found: {stats_csv}")

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else stats_csv.parent / "plots"
    )
    pgo_root = Path(args.pgo_root)

    table, simpoints = load_stats_table(stats_csv)
    if "IPC" not in table:
        raise SystemExit("IPC row missing from collected_stats.csv")
    if "Weight" not in table:
        raise SystemExit("Weight row missing from collected_stats.csv")

    if args.baseline_stats_csv:
        external_csv = Path(args.baseline_stats_csv)
        if not external_csv.is_file():
            raise SystemExit(f"external baseline stats csv not found: {external_csv}")
        replaced = apply_external_baseline_ipc(
            table,
            external_csv,
            source_config=args.baseline_config,
            target_config=args.baseline_config,
        )
        if replaced == 0:
            raise SystemExit(
                f"no baseline IPC simpoints found in external stats: {external_csv}"
            )
        print(
            f"Using external baseline IPC from {external_csv} "
            f"({replaced} simpoint(s))"
        )

    weights = build_weights(simpoints, table["Weight"])
    workloads = discover_workloads(simpoints)

    rows = summarize_configs(
        table,
        weights,
        args.baseline_config,
        pgo_root,
        args.bits_per_entry,
        workloads,
    )
    if not rows:
        raise SystemExit("no PGO configuration rows could be summarized")

    write_summary_csv(output_dir / "pgo_ifuse_summary.csv", rows)
    plot_results(
        rows,
        table,
        weights,
        args.baseline_config,
        workloads,
        output_dir,
        args.extra_stats,
    )
    print_summary(rows, args.baseline_config)
    print(f"\nWrote plots and summary under {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
