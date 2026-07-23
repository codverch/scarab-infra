#!/usr/bin/env python3
"""Simpoint-weighted IPC speedup vs baseline for Helios, Runtime iFuse, PGO iFuse, Ideal fusion.

Expects:
  {simulations}/baseline/baseline/datacenter/datacenter/<app>/<chunk>/
  {simulations}/helios/<app>/<chunk>/                                      (flat)
  {simulations}/runtime-ifuse-train-threshold-sweep/train_thresh_1000/
      datacenter/datacenter/<app>/<chunk>/
  {simulations}/pgo-ifuse/pgo_freq_1000/<app>/<chunk>/                     (flat)
  {simulations}/ideal-fusion-pass2/pass2/datacenter/datacenter/<app>/<chunk>/

Example:
  python3 hpca2027-characterization/plot_helios_ideal_ipc.py \\
    --simulations-root /users/deepmish/scarab/src/simulations \\
    --output-dir hpca2027-characterization
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

GRAPH_DIR = Path(__file__).resolve().parent
SCARAB_INFRA_ROOT = GRAPH_DIR.parent
MAIN_GRAPHS = SCARAB_INFRA_ROOT / "hpca2027-main-graphs"
SCRIPTS_DIR = SCARAB_INFRA_ROOT / "scripts"
for path in (str(MAIN_GRAPHS), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from plot_ipc import (  # noqa: E402
    AVERAGE_SEPARATOR_COLOR,
    BAR_EDGE_WIDTH,
    BAR_WIDTH,
    FONT_FAMILY,
    HELIOS_COLOR,
    IDEAL_FUSION_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_LEGEND_FONT,
    IPC_TICK_FONT,
    RFP_COLOR,
    _apply_ipc_plot_style,
    _apply_speedup_y_ticks,
    _ipc_legend_handles,
    _ylim_snap_to_tens,
    _ylim_speedup_pct_auto,
    collect_trace_pairs,
    ipc_from_sim_dir,
    load_simpoint_trace_weights,
    rename_workload,
    weighted_avg,
)
from plot_pgo_ifuse_results import SimpointKey  # noqa: E402

# Match characterization backend-stalls figure (CD/CC omitted).
WORKLOAD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GAP", ("bc", "bfs", "dfs", "pagerank", "sssp_ego_fb")),
    ("Agentic", ("appworld", "core_bench", "mlgym_fmnist", "terminal_bench")),
    ("Database", ("duckdb", "leveldb", "rocksdb")),
)
SIMPOINT_WORKLOADS = [wl for _, members in WORKLOAD_GROUPS for wl in members]

SHORT_LABELS = {
    "bc": "BC",
    "bfs": "BFS",
    "dfs": "DFS",
    "pagerank": "PR",
    "sssp_ego_fb": "SSSP",
    "appworld": "AppWorld",
    "core_bench": "CoreBench",
    "mlgym_fmnist": "MLGym",
    "terminal_bench": "TerminalBench",
    "duckdb": "DuckDB",
    "leveldb": "LevelDB",
    "rocksdb": "RocksDB",
}

# Series order left→right within each app group.
SeriesSpec = tuple[str, str, str]
DEFAULT_SCHEMES = ("helios", "runtime_ifuse", "pgo_ifuse", "ideal_fusion")
SCHEME_SPECS: dict[str, SeriesSpec] = {
    "helios": ("helios", "Helios", HELIOS_COLOR),
    "rfp": ("rfp", "RFP", RFP_COLOR),
    "runtime_ifuse": ("runtime_ifuse", "Dynamic I-Fuse", "#1565C0"),
    "pgo_ifuse": ("pgo_ifuse", "PGO-driven I-Fuse", "#009900"),
    "ideal_fusion": ("ideal_fusion", "Ideal fusion", IDEAL_FUSION_COLOR),
}
SERIES: tuple[SeriesSpec, ...] = tuple(SCHEME_SPECS[k] for k in DEFAULT_SCHEMES)

CATEGORY_GAP = 1.35
GROUP_GAP = 0.45
SUMMARY_GAP = 0.08
AXIS_FONT = 34
GROUP_FONT = 30
APP_FONT = 28
FIGSIZE = (26.0, 11.9)
GROUP_SEPARATOR_COLOR = "#666666"
SUMMARY_SEPARATOR_COLOR = "#424242"
SUMMARY_XTICK = "Average"
CHAR_BAR_WIDTH = 0.24
CHAR_END_PAD = 0.45

DEFAULT_SIM_ROOT = Path("/users/deepmish/scarab/src/simulations")
DEFAULT_TRACE_ROOT = Path("/dev/shm/baseline/simpoint_traces")
SUITE = "datacenter"
SUBSUITE = "datacenter"


def find_simpoint_dir(
    experiment_dir: Path,
    config: str,
    workload: str,
    cluster_id: str,
) -> Path | None:
    """Resolve nested (baseline/ideal/runtime) or flat (helios/pgo) layouts."""
    candidates = [
        experiment_dir / config / SUITE / SUBSUITE / workload / cluster_id,
        experiment_dir / SUITE / SUBSUITE / workload / cluster_id,
        experiment_dir / config / workload / cluster_id,
        experiment_dir / workload / cluster_id,
    ]
    for path in candidates:
        if path.is_dir() and (path / "core.stat.0.csv").is_file():
            return path
    return None


def load_experiment_ipc(
    experiment_dir: Path,
    config: str,
    sp_weights: dict[tuple[str, str], float],
    workloads: list[str],
    label: str,
) -> dict[SimpointKey, float]:
    ipc: dict[SimpointKey, float] = {}
    for workload in workloads:
        for (wl, cid), _ in sp_weights.items():
            if wl != workload:
                continue
            sim_dir = find_simpoint_dir(experiment_dir, config, wl, cid)
            if sim_dir is None:
                continue
            value = ipc_from_sim_dir(sim_dir)
            if value is None:
                continue
            ipc[SimpointKey(config, wl, cid)] = value
    print(f"  {label}: loaded {len(ipc)} IPC values from {experiment_dir}")
    return ipc


def reference_traces(
    experiment_dir: Path,
    config: str,
    workload: str,
    sp_weights: dict[tuple[str, str], float],
) -> set[str]:
    traces: set[str] = set()
    for wl, cid in sp_weights:
        if wl != workload:
            continue
        if find_simpoint_dir(experiment_dir, config, wl, cid):
            traces.add(cid)
    return traces


def check_coverage(
    epochs: dict[str, tuple[Path, str]],
    workloads: list[str],
    sp_weights: dict[tuple[str, str], float],
    report_path: Path,
    *,
    required_epochs: set[str] | None = None,
) -> tuple[set[str], dict[str, set[str]]]:
    complete: set[str] = set()
    refs: dict[str, set[str]] = {}
    baseline_dir, baseline_config = epochs["baseline"]

    with report_path.open("w") as rpt:
        for workload in workloads:
            reference = reference_traces(
                baseline_dir, baseline_config, workload, sp_weights
            )
            if not reference:
                rpt.write(f"{workload}: no baseline simpoints\n")
                continue
            refs[workload] = reference
            ok = True
            for label, (exp_dir, config) in epochs.items():
                if label == "baseline":
                    continue
                if required_epochs is not None and label not in required_epochs:
                    continue
                missing = [
                    cid
                    for cid in reference
                    if find_simpoint_dir(exp_dir, config, workload, cid) is None
                    or ipc_from_sim_dir(
                        find_simpoint_dir(exp_dir, config, workload, cid)  # type: ignore[arg-type]
                    )
                    is None
                ]
                if missing:
                    ok = False
                    rpt.write(f"{workload}/{label}: missing {missing}\n")
            if ok:
                complete.add(workload)
                rpt.write(f"{workload}: COMPLETE ({sorted(reference)})\n")
            else:
                rpt.write(f"{workload}: INCOMPLETE\n")
    return complete, refs


def _tight_x_limits(ax, x_min: float, x_max: float, *, n_series: int) -> None:
    left_pad = 0.22
    right_pad = 0.18
    half_span = (n_series * BAR_WIDTH) / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


def series_for_schemes(schemes: tuple[str, ...]) -> tuple[SeriesSpec, ...]:
    unknown = [s for s in schemes if s not in SCHEME_SPECS]
    if unknown:
        raise SystemExit(
            f"Unknown scheme(s): {unknown}. "
            f"Choose from: {', '.join(SCHEME_SPECS)}"
        )
    return tuple(SCHEME_SPECS[s] for s in schemes)


def _add_hierarchical_xaxis(
    ax,
    positions: list[float],
    app_labels: list[str],
    group_spans: list[tuple[float, float, str]],
) -> None:
    from matplotlib.transforms import blended_transform_factory

    ax.set_xticks(positions)
    ax.set_xticklabels(
        app_labels,
        rotation=45,
        ha="right",
        fontsize=APP_FONT,
        fontfamily="serif",
    )
    group_transform = blended_transform_factory(ax.transData, ax.transAxes)
    for x_start, x_end, group_name in group_spans:
        ax.text(
            (x_start + x_end) / 2.0,
            -0.40,
            group_name,
            transform=group_transform,
            ha="center",
            va="top",
            fontsize=GROUP_FONT,
            fontfamily="serif",
            fontweight="bold",
            clip_on=False,
        )


def _bar_offsets(n: int) -> list[float]:
    """Backward-compatible wrapper for other characterization scripts."""
    return [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]


def ordered_apps(apps: list[str]) -> list[str]:
    """Preserve characterization workload order without category grouping in the plot."""
    app_set = set(apps)
    ordered: list[str] = []
    for _group, members in WORKLOAD_GROUPS:
        for wl in members:
            if wl in app_set:
                ordered.append(wl)
    return ordered


def _char_bar_offsets(n: int) -> list[float]:
    return [(i - (n - 1) / 2.0) * CHAR_BAR_WIDTH for i in range(n)]


def _char_tight_x_limits(ax, x_min: float, x_max: float, *, n_bars: int) -> None:
    left_pad = 0.12
    right_pad = 0.10
    half_span = (n_bars * CHAR_BAR_WIDTH) / 2.0
    ax.set_xlim(x_min - half_span - left_pad, x_max + half_span + right_pad)
    ax.margins(x=0)


def plot_speedup(
    apps: list[str],
    series_pct: dict[str, list[float]],
    output_dir: Path,
    *,
    series: tuple[SeriesSpec, ...],
    output_stems: tuple[str, ...],
) -> None:
    import math

    apps = ordered_apps(apps)
    offsets = _char_bar_offsets(len(series))
    display_apps = [rename_workload(wl) for wl in apps] + ["Average"]
    x = list(range(len(display_apps)))

    pct_with_avg: dict[str, list[float]] = {}
    for key, _, _ in series:
        vals = list(series_pct[key])
        finite = [v for v in vals if not math.isnan(v)]
        avg = sum(finite) / len(finite) if finite else float("nan")
        pct_with_avg[key] = vals + [avg]

    pct_sets = [pct_with_avg[key] for key, _, _ in series]
    ylim = _ylim_snap_to_tens(_ylim_speedup_pct_auto(*pct_sets))

    _apply_ipc_plot_style()
    fig, ax = plt.subplots(figsize=(24, 6.5))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for (key, _label, color), offset in zip(series, offsets):
        pct_vals = pct_with_avg[key]
        ax.bar(
            [i + offset for i in x],
            [0.0 if math.isnan(val) else val for val in pct_vals],
            CHAR_BAR_WIDTH,
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
    for label in ax.get_xticklabels():
        if label.get_text() == "Average":
            label.set_weight("bold")

    _char_tight_x_limits(ax, x[0], x[-1], n_bars=len(series))
    xmin, xmax = ax.get_xlim()
    ax.set_xlim(xmin - CHAR_END_PAD, xmax + CHAR_END_PAD)

    ax.set_ylabel(
        "Speedup (%)\n(normalized to baseline)",
        fontsize=IPC_AXIS_LABEL_FONT,
        fontfamily=FONT_FAMILY,
    )
    ax.set_ylim(ylim[0], ylim[1])
    _apply_speedup_y_ticks(ax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for label in ax.get_yticklabels():
        label.set_fontfamily(FONT_FAMILY)

    legend = ax.legend(
        handles=_ipc_legend_handles(series),
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="upper left",
        fontsize=IPC_LEGEND_FONT,
        edgecolor="black",
        ncol=min(len(series), 2),
        handlelength=1.1,
        handleheight=1.1,
        framealpha=1.0,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.tight_layout()
    plt.subplots_adjust(top=1.12, bottom=0.28)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in output_stems:
        fig.savefig(output_dir / f"{stem}.png", bbox_inches="tight", dpi=300)
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight", dpi=300)
        print(f"Wrote {output_dir / stem}.png")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIM_ROOT)
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--helios-dir", type=Path, default=None)
    parser.add_argument("--rfp-dir", type=Path, default=None)
    parser.add_argument("--runtime-ifuse-dir", type=Path, default=None)
    parser.add_argument("--pgo-ifuse-dir", type=Path, default=None)
    parser.add_argument("--ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--baseline-config", default="baseline")
    parser.add_argument("--helios-config", default="helios")
    parser.add_argument("--rfp-config", default="rfp")
    parser.add_argument("--runtime-ifuse-config", default="train_thresh_10000")
    parser.add_argument("--pgo-ifuse-config", default="pgo_freq_10000")
    parser.add_argument("--ideal-fusion-config", default="pass2")
    parser.add_argument(
        "--schemes",
        nargs="+",
        choices=tuple(SCHEME_SPECS),
        default=list(DEFAULT_SCHEMES),
        help="Schemes to plot (default: helios, runtime_ifuse, pgo_ifuse, ideal_fusion)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Plot workloads with complete coverage for the selected schemes",
    )
    parser.add_argument("--output-dir", type=Path, default=GRAPH_DIR)
    parser.add_argument(
        "--output-stem",
        default=None,
        help="Output filename stem (default: ipc-<schemes joined by dash>)",
    )
    args = parser.parse_args()

    schemes = tuple(args.schemes)
    series = series_for_schemes(schemes)
    output_stem = args.output_stem or ("ipc-" + "-".join(schemes))
    output_stems = (output_stem,)

    sim_root = args.simulations_root
    baseline_dir = args.baseline_dir or (sim_root / "baseline")
    helios_dir = args.helios_dir or (sim_root / "helios")
    rfp_dir = args.rfp_dir or (sim_root / "rfp")
    runtime_dir = args.runtime_ifuse_dir or (
        sim_root / "runtime-ifuse-train-threshold-sweep"
    )
    pgo_dir = args.pgo_ifuse_dir or (sim_root / "pgo-ifuse")
    ideal_dir = args.ideal_fusion_dir or (sim_root / "ideal-fusion-pass2")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    workloads = list(SIMPOINT_WORKLOADS)
    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads)

    print("Loading IPC from simpoints...")
    baseline_ipc = load_experiment_ipc(
        baseline_dir, args.baseline_config, sp_weights, workloads, "baseline"
    )
    ipc_by_epoch: dict[str, dict] = {"baseline": baseline_ipc}
    epochs: dict[str, tuple[Path, str]] = {
        "baseline": (baseline_dir, args.baseline_config),
    }
    loaders = {
        "helios": (helios_dir, args.helios_config, "helios"),
        "rfp": (rfp_dir, args.rfp_config, "rfp"),
        "runtime_ifuse": (runtime_dir, args.runtime_ifuse_config, "runtime ifuse"),
        "pgo_ifuse": (pgo_dir, args.pgo_ifuse_config, "pgo ifuse"),
        "ideal_fusion": (ideal_dir, args.ideal_fusion_config, "ideal fusion"),
    }
    for scheme in schemes:
        exp_dir, config, label = loaders[scheme]
        ipc_by_epoch[scheme] = load_experiment_ipc(
            exp_dir, config, sp_weights, workloads, label
        )
        epochs[scheme] = (exp_dir, config)
    config_by_epoch = {k: cfg for k, (_d, cfg) in epochs.items()}

    complete, refs = check_coverage(
        epochs,
        workloads,
        sp_weights,
        output_dir / "ipc_simpoint_coverage.txt",
        required_epochs=set(schemes),
    )
    if not complete:
        if not args.allow_partial:
            raise SystemExit(
                "No apps have complete coverage for the selected schemes. "
                "Use --allow-partial to plot apps with complete data."
            )
        print("Warning: no app has complete coverage; plotting nothing.")
        return

    apps = [wl for wl in workloads if wl in complete]
    series_pct: dict[str, list[float]] = {key: [] for key, _, _ in series}
    rows: list[dict[str, float | str]] = []

    header_keys = [key for key, _, _ in series]
    print(
        f"\n{'App':<18} {'BaseIPC':>8} "
        + " ".join(f"{k:>10}" for k in header_keys)
        + " "
        + " ".join(f"{k+'%':>10}" for k in header_keys)
    )
    print("-" * 120)

    for wl in apps:
        ref = refs[wl]
        avgs: dict[str, float] = {}
        for epoch_key, ipc_map in ipc_by_epoch.items():
            pairs = collect_trace_pairs(
                ipc_map,
                sp_weights,
                config=config_by_epoch[epoch_key],
                workload=wl,
                reference_traces=ref,
            )
            avgs[epoch_key] = weighted_avg(pairs)

        b_avg = avgs["baseline"]
        row: dict[str, float | str] = {"workload": wl, "baseline_ipc": b_avg}
        pct_parts: list[str] = []
        ipc_parts: list[str] = [f"{b_avg:8.3f}"]
        for key, _label, _color in series:
            avg = avgs[key]
            norm = avg / b_avg if b_avg else float("nan")
            pct = 100.0 * (norm - 1.0)
            series_pct[key].append(pct)
            row[f"{key}_ipc"] = avg
            row[f"{key}_norm"] = norm
            row[f"{key}_speedup_pct"] = pct
            ipc_parts.append(f"{avg:10.3f}")
            pct_parts.append(f"{pct:+9.2f}%")
        rows.append(row)
        print(f"{wl:<18} " + " ".join(ipc_parts) + " " + " ".join(pct_parts))

    avgs_pct = {key: float(np.mean(series_pct[key])) for key, _, _ in series}
    print("-" * 120)
    print(
        f"{'Average':<18} {'':>8} "
        + " ".join(f"{'':>10}" for _ in series)
        + " "
        + " ".join(f"{avgs_pct[k]:+9.2f}%" for k, _, _ in series)
    )

    csv_path = output_dir / f"{output_stem}.csv"
    fieldnames = ["workload", "baseline_ipc"]
    for key, _, _ in series:
        fieldnames.extend([f"{key}_ipc", f"{key}_norm", f"{key}_speedup_pct"])
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        avg_row: dict[str, float | str] = {"workload": "Average", "baseline_ipc": ""}
        for key, _, _ in series:
            avg_row[f"{key}_ipc"] = ""
            avg_row[f"{key}_norm"] = ""
            avg_row[f"{key}_speedup_pct"] = avgs_pct[key]
        writer.writerow(avg_row)
    print(f"Wrote {csv_path}")

    plot_speedup(
        apps,
        series_pct,
        output_dir,
        series=series,
        output_stems=output_stems,
    )


if __name__ == "__main__":
    main()
