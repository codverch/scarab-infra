#!/usr/bin/env python3
"""L1-D read-port sensitivity: I-Fuse and ideal-fusion speedup at 1/2/3 read ports.

For each port count P, speedup is measured against the baseline with the same
number of L1-D read ports (--dcache_read_ports P):

  speedup_pct = 100 * (weighted_ipc(config, P) / weighted_ipc(baseline, P) - 1)

weighted_ipc is the simpoint-weighted arithmetic mean of Periodic IPC over the
simpoints present in both the baseline and the config for that port count
(same convention as plot_ipc.py). Average = arithmetic mean of per-app ratios.

Layout under --simulations-root (hpca2027-main):
  1 port : baseline/, ifuse/, ideal-fusion/           (override with --one-port-*)
  P ports: P-L1-D-read-ports/{baseline,ifuse,ideal-fusion}/

Example:

/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-main-graphs/plot_l1d_read_ports_ipc.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --trace-root /dev/shm/ifuse/simpoint_traces
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Patch

GRAPH_DIR = Path(__file__).resolve().parent
if str(GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(GRAPH_DIR))

import plot_ipc  # noqa: E402
from plot_ipc import (  # noqa: E402
    AVERAGE_GAP,
    AVERAGE_SEPARATOR_COLOR,
    AVERAGE_SEPARATOR_WIDTH,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_SIMULATIONS_ROOT,
    DEFAULT_SUBSUITE,
    DEFAULT_SUITE,
    DEFAULT_TRACE_ROOT,
    IDEAL_FUSION_COLOR,
    IFUSE_COLOR,
    IPC_AXIS_LABEL_FONT,
    IPC_FIGSIZE,
    IPC_TICK_FONT,
    SIMPOINT_WORKLOADS,
    find_simpoint_dir,
    ipc_from_sim_dir,
    load_simpoint_trace_weights,
    order_workloads_by_group,
    register_noto_serif,
    rename_workload,
)

PORT_COUNTS = (1, 2, 3)
PORT_HATCHES = {1: "", 2: "//", 3: "xx"}
# (config key, legend label, color)
SCHEMES: tuple[tuple[str, str, str], ...] = (
    ("ifuse", "I-Fuse", IFUSE_COLOR),
    ("ideal-fusion", "Ideal fusion", IDEAL_FUSION_COLOR),
)

DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "l1d_read_ports_ipc"
OUTPUT_STEM = "l1d-read-ports-ipc"

BAR_WIDTH = 1.2
APP_STEP = 10.0
BAR_EDGE_WIDTH = 4.5
HATCH_WIDTH = 3.0
LEGEND_FONT = 90
Y_AXIS_LABEL = "Speedup over\nsame-port\nbaseline (%)"
OUTPUT_DPI = 300


def port_dirs(args: argparse.Namespace) -> dict[int, dict[str, Path]]:
    root = args.simulations_root
    dirs: dict[int, dict[str, Path]] = {
        1: {
            "baseline": args.one_port_baseline_dir or root / "baseline",
            "ifuse": args.one_port_ifuse_dir or root / "ifuse",
            "ideal-fusion": args.one_port_ideal_fusion_dir or root / "ideal-fusion",
        }
    }
    for ports in PORT_COUNTS[1:]:
        base = root / f"{ports}-L1-D-read-ports"
        dirs[ports] = {cfg: base / cfg for cfg in ("baseline", "ifuse", "ideal-fusion")}
    return dirs


def weighted_ipc(
    exp_dir: Path,
    workload: str,
    cluster_ids: list[str],
    sp_weights: dict[tuple[str, str], float],
) -> float:
    total_w = 0.0
    total = 0.0
    for cid in cluster_ids:
        sim_dir = find_simpoint_dir(
            exp_dir, exp_dir.name, workload, cid, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE
        )
        ipc = ipc_from_sim_dir(sim_dir) if sim_dir else None
        if ipc is None:
            return float("nan")
        weight = sp_weights[(workload, cid)]
        total += weight * ipc
        total_w += weight
    return total / total_w if total_w > 0 else float("nan")


def present_ids(
    exp_dir: Path, workload: str, sp_weights: dict[tuple[str, str], float]
) -> set[str]:
    return {
        cid
        for (wl, cid) in sp_weights
        if wl == workload
        and (
            sim_dir := find_simpoint_dir(
                exp_dir, exp_dir.name, wl, cid, suite=DEFAULT_SUITE, subsuite=DEFAULT_SUBSUITE
            )
        )
        is not None
        and ipc_from_sim_dir(sim_dir) is not None
    }


def compute_speedups(
    workloads: list[str],
    dirs: dict[int, dict[str, Path]],
    sp_weights: dict[tuple[str, str], float],
    log_lines: list[str],
) -> dict[tuple[str, int], dict[str, float]]:
    """Return {(scheme, ports): {workload: ipc_ratio}} (NaN when data is missing)."""
    ratios: dict[tuple[str, int], dict[str, float]] = {}
    for ports in PORT_COUNTS:
        for scheme, label, _ in SCHEMES:
            per_wl: dict[str, float] = {}
            for wl in workloads:
                base_dir, cfg_dir = dirs[ports]["baseline"], dirs[ports][scheme]
                common = present_ids(base_dir, wl, sp_weights) & present_ids(cfg_dir, wl, sp_weights)
                if not common:
                    per_wl[wl] = float("nan")
                    log_lines.append(f"{ports}p {label:12s} {wl:14s} MISSING ({cfg_dir})")
                    continue
                ids = sorted(common, key=lambda c: int(c) if c.isdigit() else c)
                base_ipc = weighted_ipc(base_dir, wl, ids, sp_weights)
                cfg_ipc = weighted_ipc(cfg_dir, wl, ids, sp_weights)
                per_wl[wl] = cfg_ipc / base_ipc if base_ipc > 0 else float("nan")
                log_lines.append(
                    f"{ports}p {label:12s} {wl:14s} simpoints={','.join(ids):24s} "
                    f"baseline_ipc={base_ipc:.4f} ipc={cfg_ipc:.4f} "
                    f"speedup={(per_wl[wl] - 1) * 100:+.2f}%"
                )
            ratios[(scheme, ports)] = per_wl
    return ratios


def _mean(values: list[float]) -> float:
    vals = [v for v in values if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def plot(
    workloads: list[str],
    ratios: dict[tuple[str, int], dict[str, float]],
    output_dir: Path,
) -> None:
    series = [(scheme, ports, color) for scheme, _, color in SCHEMES for ports in PORT_COUNTS]
    n = len(series)
    offsets = [(i - (n - 1) / 2.0) * BAR_WIDTH for i in range(n)]
    x_map = {wl: i * APP_STEP for i, wl in enumerate(workloads)}
    cluster_half = n * BAR_WIDTH / 2.0
    avg_x = x_map[workloads[-1]] + 2 * cluster_half + AVERAGE_GAP
    separator_x = x_map[workloads[-1]] + cluster_half + AVERAGE_GAP / 2.0

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": [plot_ipc.FONT_FAMILY, "DejaVu Serif", "serif"],
            "hatch.linewidth": HATCH_WIDTH,
        }
    )
    fig, ax = plt.subplots(figsize=(IPC_FIGSIZE[0], IPC_FIGSIZE[1] + 6.0))
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    y_max = 0.0
    for (scheme, ports, color), offset in zip(series, offsets):
        per_wl = ratios[(scheme, ports)]
        vals = [(per_wl[wl] - 1) * 100 for wl in workloads]
        vals.append((_mean(list(per_wl.values())) - 1) * 100)
        xs = [x_map[wl] + offset for wl in workloads] + [avg_x + offset]
        keep = [(x, v) for x, v in zip(xs, vals) if not math.isnan(v)]
        if not keep:
            continue
        ax.bar(
            [x for x, _ in keep],
            [v for _, v in keep],
            BAR_WIDTH,
            color=color,
            hatch=PORT_HATCHES[ports],
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            zorder=3,
        )
        y_max = max(y_max, max(v for _, v in keep))

    ax.axhline(0.0, color="black", linewidth=BAR_EDGE_WIDTH, zorder=4)
    ax.axvline(
        separator_x,
        color=AVERAGE_SEPARATOR_COLOR,
        linestyle="--",
        linewidth=AVERAGE_SEPARATOR_WIDTH,
        zorder=2,
    )
    ax.set_xticks([x_map[wl] for wl in workloads] + [avg_x])
    ax.set_xticklabels([rename_workload(wl) for wl in workloads] + ["Average"], rotation=45, ha="right")
    for label in ax.get_xticklabels():
        label.set_fontsize(IPC_TICK_FONT)
        if label.get_text() == "Average":
            label.set_weight("bold")
    ax.set_xlim(-cluster_half - 2.5, avg_x + cluster_half + 2.5)
    ax.set_ylabel(Y_AXIS_LABEL, fontsize=IPC_AXIS_LABEL_FONT, labelpad=28)
    step = 10 if y_max <= 60 else 20
    ax.set_ylim(min(0.0, ax.get_ylim()[0]), math.ceil((y_max + 1) / step) * step)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(step))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="x", labelsize=IPC_TICK_FONT, length=0, pad=14)
    ax.tick_params(axis="y", labelsize=IPC_TICK_FONT)
    for spine in ax.spines.values():
        spine.set_linewidth(BAR_EDGE_WIDTH)

    handles = [
        Patch(facecolor=color, edgecolor="black", linewidth=BAR_EDGE_WIDTH, label=label)
        for _, label, color in SCHEMES
    ] + [
        Patch(
            facecolor="white",
            edgecolor="black",
            linewidth=BAR_EDGE_WIDTH,
            hatch=PORT_HATCHES[p],
            label=f"{p} read port{'s' if p > 1 else ''}",
        )
        for p in PORT_COUNTS
    ]
    plt.subplots_adjust(top=0.62, bottom=0.30, left=0.12, right=0.98)
    legend = ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.08),
        ncol=len(handles),
        fontsize=LEGEND_FONT,
        frameon=True,
        fancybox=False,
        edgecolor="black",
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.2,
        borderaxespad=0.0,
    )
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{OUTPUT_STEM}.{ext}", dpi=OUTPUT_DPI, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def main() -> None:
    register_noto_serif()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--simulations-root", type=Path, default=DEFAULT_SIMULATIONS_ROOT)
    parser.add_argument("--one-port-baseline-dir", type=Path, default=None)
    parser.add_argument("--one-port-ifuse-dir", type=Path, default=None)
    parser.add_argument("--one-port-ideal-fusion-dir", type=Path, default=None)
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument(
        "--workloads-db",
        type=Path,
        default=None,
        help="use workloads_db.json cluster weights instead of trace-root opt.p/opt.w",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--exclude-workloads", nargs="*", default=["feedsim", "langchain_web"])
    args = parser.parse_args()

    workloads = order_workloads_by_group(
        [wl for wl in SIMPOINT_WORKLOADS if wl not in set(args.exclude_workloads)]
    )
    sp_weights = load_simpoint_trace_weights(args.trace_root, workloads, workloads_db=args.workloads_db)
    dirs = port_dirs(args)

    log_lines: list[str] = []
    ratios = compute_speedups(workloads, dirs, sp_weights, log_lines)
    workloads = [wl for wl in workloads if any(not math.isnan(r[wl]) for r in ratios.values())]

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{OUTPUT_STEM}_computation_log.txt").write_text("\n".join(log_lines) + "\n")
    with (output_dir / f"{OUTPUT_STEM}_summary.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["workload", "display_name", "scheme", "read_ports", "speedup_pct"])
        for (scheme, ports), per_wl in ratios.items():
            for wl in workloads + ["Average"]:
                ratio = _mean(list(per_wl.values())) if wl == "Average" else per_wl[wl]
                name = "Average" if wl == "Average" else rename_workload(wl)
                writer.writerow([wl, name, scheme, ports, f"{(ratio - 1) * 100:.2f}"])

    plot(workloads, ratios, output_dir)

    print("\n".join(log_lines))
    print("\nAverage speedup over same-port baseline:")
    for (scheme, ports), per_wl in ratios.items():
        print(f"  {scheme:12s} {ports} port(s): {(_mean(list(per_wl.values())) - 1) * 100:+.2f}%")
    print(f"\nOutputs in {output_dir}")


if __name__ == "__main__":
    main()
