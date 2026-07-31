#!/usr/bin/env python3
"""BFS IPC for baseline vs runtime I-Fuse across ROB sizes 128 / 192 / 256 / 512.

ROB 128/192/256 use simpoint-weighted IPC from
  src/rob-varying-size-results/{baseline,ifuse}-rob{SIZE}/bfs-init/
ROB 512 uses the main sims (NODE_TABLE_SIZE=512):
  src/simulations/{baseline,ifuse}/bfs/

Commands:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_rob_size_bfs_ipc.py \
  --results-root /users/deepmish/scarab/src/rob-varying-size-results \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir /users/deepmish/scarab/src/rob-varying-size-results
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

from plot_ipc import (  # noqa: E402
    BAR_EDGE_WIDTH,
    BASELINE_COLOR,
    DEFAULT_SCARAB_ROOT,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_WORKLOADS_DB,
    FONT_FAMILY,
    IFUSE_COLOR,
    _apply_ipc_plot_style,
    ipc_from_sim_dir,
    register_noto_serif,
)

ROB_SIZES = (128, 192, 256, 512)
WEIGHT_WORKLOAD = "bfs"
SERIES = (
    ("baseline", "No-fusion", BASELINE_COLOR),
    ("ifuse", "I-Fuse", IFUSE_COLOR),
)

# Match plot_topdown_backend_stalls.py sizing (scaled for 4 ROB groups vs ~12 apps).
AXIS_FONT = 37
BAR_WIDTH = 0.40
FIGSIZE = (8.0, 7.0)
Y_LABEL_PAD = 20



def load_bfs_weights(workloads_db: Path) -> dict[str, float]:
    with workloads_db.open() as fh:
        db = json.load(fh)
    entry = db.get("datacenter", {}).get("datacenter", {}).get(WEIGHT_WORKLOAD)
    if not entry:
        raise SystemExit(f"No simpoint weights for {WEIGHT_WORKLOAD} in {workloads_db}")
    weights: dict[str, float] = {}
    for sp in entry.get("simpoints", []):
        cid = str(sp["cluster_id"])
        w = float(sp["weight"])
        if w > 0:
            weights[cid] = w
    if not weights:
        raise SystemExit(f"Empty simpoint weights for {WEIGHT_WORKLOAD}")
    return weights


def experiment_dirs(
    rob: int,
    *,
    results_root: Path,
    simulations_root: Path,
) -> tuple[Path, Path, str]:
    """Return (baseline_dir, ifuse_dir, workload_subdir)."""
    if rob == 512:
        return (
            simulations_root / "baseline",
            simulations_root / "ifuse",
            "bfs",
        )
    return (
        results_root / f"baseline-rob{rob}",
        results_root / f"ifuse-rob{rob}",
        "bfs-init",
    )


def weighted_ipc(
    experiment_dir: Path,
    workload: str,
    weights: dict[str, float],
) -> tuple[float, int]:
    weighted_sum = 0.0
    weight_sum = 0.0
    count = 0
    for cluster_id, weight in weights.items():
        sim_dir = experiment_dir / workload / cluster_id
        ipc = ipc_from_sim_dir(sim_dir)
        if ipc is None:
            continue
        weighted_sum += weight * ipc
        weight_sum += weight
        count += 1
    if count == 0 or weight_sum <= 0:
        raise SystemExit(f"No IPC data under {experiment_dir / workload}")
    return weighted_sum / weight_sum, count


def write_summary_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "rob_size",
        "baseline_ipc",
        "ifuse_ipc",
        "ifuse_speedup",
        "ifuse_speedup_pct",
        "simpoints",
        "baseline_dir",
        "ifuse_dir",
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def plot_bfs_rob_ipc(rows: list[dict[str, object]], output_dir: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    rob_labels = [str(r["rob_size"]) for r in rows]
    x = list(range(len(rows)))
    offsets = [-(BAR_WIDTH / 2.0), BAR_WIDTH / 2.0]

    baseline_vals = [float(r["baseline_ipc"]) for r in rows]
    ifuse_vals = [float(r["ifuse_ipc"]) for r in rows]
    series_vals = [baseline_vals, ifuse_vals]

    _apply_ipc_plot_style()
    plt.rcParams.update(
        {
            "axes.labelsize": AXIS_FONT,
            "xtick.labelsize": AXIS_FONT,
            "ytick.labelsize": AXIS_FONT,
            "legend.fontsize": AXIS_FONT,
        }
    )
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    for offset, (_key, _label, color), values in zip(offsets, SERIES, series_vals):
        ax.bar(
            [i + offset for i in x],
            values,
            BAR_WIDTH,
            color=color,
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(rob_labels, fontsize=AXIS_FONT, fontfamily=FONT_FAMILY)
    ax.tick_params(axis="both", labelsize=AXIS_FONT)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontsize(AXIS_FONT)
        label.set_fontfamily(FONT_FAMILY)

    # Same tight-x padding style as backend-stalls (single-bar width).
    left_pad = 0.40
    right_pad = 0.12
    half_span = BAR_WIDTH
    ax.set_xlim(x[0] - half_span - left_pad, x[-1] + half_span + right_pad)
    ax.margins(x=0)

    ax.set_xlabel("ROB size", fontsize=AXIS_FONT, fontfamily=FONT_FAMILY)
    ax.set_ylabel(
        "Instructions Per\nCycle (IPC)",
        fontsize=AXIS_FONT,
        fontfamily=FONT_FAMILY,
        labelpad=Y_LABEL_PAD,
    )

    ymax = max(max(baseline_vals), max(ifuse_vals))
    ax.set_ylim(0.0, ymax * 1.15)

    legend = ax.legend(
        handles=[
            Patch(
                facecolor=color,
                edgecolor="black",
                linewidth=BAR_EDGE_WIDTH,
                label=label,
            )
            for _key, label, color in SERIES
        ],
        frameon=True,
        fancybox=False,
        shadow=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        bbox_transform=ax.transAxes,
        fontsize=AXIS_FONT,
        edgecolor="black",
        ncol=2,
        handlelength=0.95,
        handleheight=0.95,
        borderpad=0.45,
        columnspacing=0.9,
        framealpha=1.0,
    )
    legend.set_clip_on(False)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    legend.get_frame().set_facecolor("white")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(2.5)

    plt.subplots_adjust(top=0.82, bottom=0.18, left=0.22, right=0.98)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("bfs_rob_size_ipc",):
        out = output_dir / stem
        fig.savefig(f"{out}.png", bbox_inches="tight", pad_inches=0.08, dpi=300)
        fig.savefig(f"{out}.pdf", bbox_inches="tight", pad_inches=0.08, dpi=300)
        fig.savefig(f"{out}.eps", bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(
        description="Plot BFS IPC for baseline vs I-Fuse across ROB sizes."
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_SCARAB_ROOT / "src" / "rob-varying-size-results",
        help="Root for ROB 128/192/256 sweep dirs",
    )
    parser.add_argument(
        "--simulations-root",
        type=Path,
        default=DEFAULT_SIMULATIONS_ROOT,
        help="Root for ROB 512 baseline/ifuse sims (default: simulations/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Default: --results-root",
    )
    parser.add_argument("--workloads-db", type=Path, default=DEFAULT_WORKLOADS_DB)
    args = parser.parse_args()

    results_root = args.results_root
    simulations_root = args.simulations_root
    output_dir = args.output_dir or results_root
    weights = load_bfs_weights(args.workloads_db)

    print(f"Simpoint weights ({WEIGHT_WORKLOAD}): {weights}")
    print(f"Sweep root (128/192/256): {results_root}")
    print(f"ROB 512 sims: {simulations_root}/{{baseline,ifuse}}/bfs")

    rows: list[dict[str, object]] = []
    for rob in ROB_SIZES:
        baseline_dir, ifuse_dir, workload = experiment_dirs(
            rob, results_root=results_root, simulations_root=simulations_root
        )
        baseline_ipc, n_base = weighted_ipc(baseline_dir, workload, weights)
        ifuse_ipc, n_ifuse = weighted_ipc(ifuse_dir, workload, weights)
        if n_base != n_ifuse:
            print(f"  warning rob={rob}: baseline simpoints={n_base}, ifuse={n_ifuse}")
        speedup = ifuse_ipc / baseline_ipc if baseline_ipc > 0 else float("nan")
        rows.append(
            {
                "rob_size": rob,
                "baseline_ipc": f"{baseline_ipc:.6f}",
                "ifuse_ipc": f"{ifuse_ipc:.6f}",
                "ifuse_speedup": f"{speedup:.6f}",
                "ifuse_speedup_pct": f"{100.0 * (speedup - 1.0):.4f}",
                "simpoints": n_base,
                "baseline_dir": str(baseline_dir / workload),
                "ifuse_dir": str(ifuse_dir / workload),
            }
        )
        print(
            f"  ROB {rob}: baseline={baseline_ipc:.4f}  ifuse={ifuse_ipc:.4f}  "
            f"speedup={speedup:.4f}x (+{100.0 * (speedup - 1.0):.2f}%)  "
            f"(simpoints={n_base}, workload={workload})"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_summary_csv(output_dir / "bfs_rob_size_ipc_summary.csv", rows)
    plot_bfs_rob_ipc(rows, output_dir)

    print("\nOutputs:")
    print(f"  - {output_dir / 'bfs_rob_size_ipc.png'}")
    print(f"  - {output_dir / 'bfs_rob_size_ipc.pdf'}")
    print(f"  - {output_dir / 'bfs_rob_size_ipc.eps'}")
    print(f"  - {output_dir / 'bfs_rob_size_ipc_summary.csv'}")


if __name__ == "__main__":
    main()
