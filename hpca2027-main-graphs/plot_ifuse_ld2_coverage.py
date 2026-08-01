#!/usr/bin/env python3
"""Plot I-Fuse load coverage and demand loads: before vs after LD2 fuse."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

import plot_ipc  # noqa: E402
from plot_ipc import (  # noqa: E402
    APP_STEP,
    AVERAGE_GAP,
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    BAR_EDGE_WIDTH,
    BAR_WIDTH,
    DEFAULT_RESULTS_ROOT,
    FONT_FAMILY,
    IFUSE_COLOR,
    IPC_AXIS_FONT,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    _annotate_ipc_bar_labels,
    _apply_ipc_plot_style,
    _apply_speedup_y_grid,
    _bar_offsets,
    _draw_app_x_tick_guides,
    _ipc_legend_handles,
    _tight_x_limits,
    _ylim_snap_to_tens,
    _ylim_with_bar_label_headroom,
    grouped_x_positions,
    order_workloads_by_group,
    rename_workload,
)

BEFORE_COLOR = "#4C78A8"
AFTER_COLOR = IFUSE_COLOR
LD2_SERIES = (
    ("before", "Before fusing LD2", BEFORE_COLOR),
    ("after", "After fusing LD2", AFTER_COLOR),
)

APPS = [
    ("bfs", "bfs", "bfs-init", "bfs"),
    ("dfs", "dfs", "dfs-init", "dfs"),
    ("pagerank", "pagerank", "pagerank-init", "pagerank"),
    ("core_bench", "core_bench", "corebench", "corebench"),
    ("appworld", "appworld", "appworld", "appworld"),
    ("terminal_bench", "terminal_bench", "terminal_bench", "terminal_bench"),
    ("clickhouse", "clickhouse", "clickhouse", "clickhouse"),
    ("duckdb", "duckdb", "duckdb", "duckdb"),
    ("leveldb", "leveldb", "leveldb", "leveldb"),
    ("memcached", "memcached", "memcached", "memcached"),
]

SCARAB = Path("/users/deepmish/scarab")
AFTER_DIR = SCARAB / "src" / "simulations" / "ifuse"
BEFORE_GIT_REF = "eaea6ca73d93eb9b56aa3584803723fcb00ee880"
BEFORE_GIT_BASE = "src/simulations/ifuse"
WORKLOADS_DB = Path("/users/deepmish/scarab-infra/workloads/workloads_db.json")
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "ifuse_ld2_coverage"
NOTO_SERIF_FONT = GRAPH_DIR / "fonts" / "NotoSerif.ttf"
IFUSE_STAT = "ifuse.stat.0.csv"


def register_noto_serif() -> None:
    from matplotlib import font_manager

    font_manager.fontManager.addfont(str(NOTO_SERIF_FONT))
    plot_ipc.FONT_FAMILY = font_manager.FontProperties(fname=str(NOTO_SERIF_FONT)).get_name()


def git_show(path: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(SCARAB), "show", f"{BEFORE_GIT_REF}:{path}"],
            text=True,
            errors="replace",
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def list_cids_git(app: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(SCARAB), "ls-tree", "--name-only", f"{BEFORE_GIT_REF}:{BEFORE_GIT_BASE}/{app}"],
            text=True,
            errors="replace",
        )
    except subprocess.CalledProcessError:
        return []
    return sorted((x for x in out.splitlines() if x.isdigit()), key=int)


def get_stat(txt: str | None, name: str) -> float | None:
    if not txt:
        return None
    for row in csv.reader(txt.splitlines()):
        if len(row) >= 3 and row[0].strip() == name:
            try:
                return float(row[2].strip())
            except ValueError:
                return None
    return None


def weights(app: str) -> dict[str, float]:
    db = json.loads(WORKLOADS_DB.read_text())["datacenter"]["datacenter"]
    return {
        str(sp["cluster_id"]): float(sp["weight"])
        for sp in db.get(app, {}).get("simpoints", [])
    }


def weighted_ifuse(mode: str, app: str, weight_app: str) -> dict[str, float] | None:
    names = (
        "IFUSE_ALL_LOADS_count",
        "IFUSE_NUM_FUSED_LOADS_count",
        "IFUSE_NUM_DEMAND_LOADS_count",
        "IFUSE_TRAINING_PAIRS_DISCOVERED_count",
    )
    wmap = weights(weight_app)
    if mode == "before":
        cids = list_cids_git(app)
    else:
        root = AFTER_DIR / app
        cids = sorted(
            (p.name for p in root.iterdir() if p.is_dir() and p.name.isdigit()),
            key=int,
        )
    acc = {n: 0.0 for n in names}
    den = 0.0
    for cid in cids:
        w = wmap.get(cid)
        if w is None:
            continue
        if mode == "before":
            txt = git_show(f"{BEFORE_GIT_BASE}/{app}/{cid}/{IFUSE_STAT}")
        else:
            p = AFTER_DIR / app / cid / IFUSE_STAT
            txt = p.read_text(errors="replace") if p.is_file() else None
        vals = {n: get_stat(txt, n) for n in names}
        if any(v is None for v in vals.values()):
            continue
        for n, v in vals.items():
            acc[n] += w * float(v)
        den += w
    if den <= 0:
        return None
    return {n: acc[n] / den for n in names}


def plot_pct_bars(
    workloads: list[str],
    series_pct: dict[str, list[float]],
    output_dir: Path,
    *,
    ylabel: str,
    file_prefix: str,
) -> None:
    import matplotlib.pyplot as plt

    # Reuse speedup bar machinery via fake normalized ratios.
    series_norm = {
        k: [1.0 + v / 100.0 for v in vals] for k, vals in series_pct.items()
    }
    from plot_ipc import plot_speedup_bars

    plot_speedup_bars(
        workloads,
        series_norm,
        output_dir,
        series=LD2_SERIES,
        figure_height=10.0,
        legend_outside=True,
        ylabel=ylabel,
        file_prefix=file_prefix,
    )


def plot_absolute_bars(
    workloads: list[str],
    series_vals: dict[str, list[float]],
    output_dir: Path,
    *,
    ylabel: str,
    file_prefix: str,
    scale: float = 1e6,
    unit_label: str = "M",
) -> None:
    import matplotlib.pyplot as plt

    ordered = order_workloads_by_group(workloads)
    by_idx = {wl: i for i, wl in enumerate(workloads)}
    series_scaled = {
        k: [series_vals[k][by_idx[wl]] / scale for wl in ordered]
        for k, _, _ in LD2_SERIES
    }
    for k, _, _ in LD2_SERIES:
        vals = series_scaled[k]
        series_scaled[k] = vals + [sum(vals) / len(vals)]

    offsets = _bar_offsets(len(LD2_SERIES))
    _, x_map, avg_x, separator_x = grouped_x_positions(ordered, n_series=len(LD2_SERIES))
    display = [rename_workload(wl) for wl in ordered] + ["Average"]
    x_ticks = [x_map[wl] for wl in ordered] + [avg_x]
    ymax = max(max(series_scaled[k]) for k, _, _ in LD2_SERIES)

    for stem, show_labels, ylim in (
        (f"{file_prefix}-labeled", True, (0.0, ymax * 1.22)),
        (file_prefix, False, (0.0, ymax * 1.08)),
    ):
        _apply_ipc_plot_style()
        fig_w = max(22.0, len(x_ticks) * APP_STEP * 1.15 + AVERAGE_GAP)
        fig, ax = plt.subplots(figsize=(fig_w, 10.0))
        for (key, _lab, color), offset in zip(LD2_SERIES, offsets):
            vals = series_scaled[key]
            bar_x = [x_map[wl] + offset for wl in ordered] + [avg_x + offset]
            container = ax.bar(
                bar_x,
                vals,
                BAR_WIDTH,
                color=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                zorder=3,
            )
            if show_labels:
                for patch, val in zip(container.patches, vals):
                    x = patch.get_x() + patch.get_width() / 2
                    ax.text(
                        x,
                        val + ymax * 0.015,
                        f"{val:.2f}{unit_label}",
                        ha="center",
                        va="bottom",
                        fontsize=IPC_AXIS_FONT,
                        fontfamily=plot_ipc.FONT_FAMILY,
                        rotation=90,
                        zorder=4,
                    )
        ax.axvline(
            separator_x,
            color=AVERAGE_SEPARATOR_COLOR,
            linestyle="--",
            linewidth=AVERAGE_SEPARATOR_WIDTH,
            zorder=2,
        )
        ax.set_xticks(x_ticks)
        ax.set_xticklabels(display, rotation=45, ha="right", fontsize=IPC_TICK_FONT)
        ax.get_xticklabels()[-1].set_weight("bold")
        _tight_x_limits(ax, x_ticks[0], x_ticks[-1], n_bars=len(LD2_SERIES))
        ax.set_ylabel(ylabel, fontsize=IPC_AXIS_LABEL_FONT, fontfamily=plot_ipc.FONT_FAMILY)
        ax.set_ylim(ylim)
        _apply_speedup_y_grid(ax)
        _draw_app_x_tick_guides(ax, x_ticks)
        ax.legend(
            handles=_ipc_legend_handles(LD2_SERIES),
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=2,
            fontsize=IPC_LEGEND_FONT,
            edgecolor="black",
            framealpha=1.0,
        )
        for spine in ax.spines.values():
            spine.set_linewidth(2.5)
        plt.tight_layout()
        plt.subplots_adjust(top=0.80, bottom=0.22, right=0.98)
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", dpi=300)
        plt.close(fig)


def main() -> None:
    out = DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    register_noto_serif()

    rows = []
    for key, bapp, oapp, napp in APPS:
        before = weighted_ifuse("before", oapp, bapp)
        after = weighted_ifuse("after", napp, bapp)
        if before is None or after is None:
            raise SystemExit(f"Missing ifuse stats for {key}")
        cov_b = 100.0 * before["IFUSE_NUM_FUSED_LOADS_count"] / before["IFUSE_ALL_LOADS_count"]
        cov_a = 100.0 * after["IFUSE_NUM_FUSED_LOADS_count"] / after["IFUSE_ALL_LOADS_count"]
        rows.append(
            {
                "workload": key,
                "cov_before": cov_b,
                "cov_after": cov_a,
                "demand_before": before["IFUSE_NUM_DEMAND_LOADS_count"],
                "demand_after": after["IFUSE_NUM_DEMAND_LOADS_count"],
                "fused_before": before["IFUSE_NUM_FUSED_LOADS_count"],
                "fused_after": after["IFUSE_NUM_FUSED_LOADS_count"],
                "all_before": before["IFUSE_ALL_LOADS_count"],
                "all_after": after["IFUSE_ALL_LOADS_count"],
                "train_before": before["IFUSE_TRAINING_PAIRS_DISCOVERED_count"],
                "train_after": after["IFUSE_TRAINING_PAIRS_DISCOVERED_count"],
            }
        )
        print(
            f"{rename_workload(key):14s}  "
            f"cov={cov_b:.1f}->{cov_a:.1f}%  "
            f"demand={before['IFUSE_NUM_DEMAND_LOADS_count']/1e6:.2f}->{after['IFUSE_NUM_DEMAND_LOADS_count']/1e6:.2f}M  "
            f"train={before['IFUSE_TRAINING_PAIRS_DISCOVERED_count']:.0f}->{after['IFUSE_TRAINING_PAIRS_DISCOVERED_count']:.0f}"
        )

    ordered = order_workloads_by_group([r["workload"] for r in rows])
    by = {r["workload"]: r for r in rows}
    rows = [by[wl] for wl in ordered]
    workloads = [r["workload"] for r in rows]

    with (out / "coverage_demand_summary.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    plot_pct_bars(
        workloads,
        {
            "before": [r["cov_before"] for r in rows],
            "after": [r["cov_after"] for r in rows],
        },
        out,
        ylabel="I-Fuse load coverage (%)\n(fused / all loads)",
        file_prefix="coverage",
    )
    plot_absolute_bars(
        workloads,
        {
            "before": [r["demand_before"] for r in rows],
            "after": [r["demand_after"] for r in rows],
        },
        out,
        ylabel="I-Fuse demand loads\n(IFUSE_NUM_DEMAND_LOADS)",
        file_prefix="demand_loads",
    )
    print(f"\nWrote plots under {out}")


if __name__ == "__main__":
    main()
