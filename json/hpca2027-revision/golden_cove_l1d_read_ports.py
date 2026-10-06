#!/usr/bin/env python3
"""Golden Cove baseline vs 1-cycle-delayed I-Fuse at 1, 2 and 3 L1-D read ports.

The 2- and 3-port runs reuse each app's committed 1-port PARAMS (PARAMS.golden_cove,
memtrace, 20M warmup + 100M measured) with only --dcache_read_ports changed:
  baseline  scarab hpca2027-revision-ideal-fusion (b373e0fad), --ideal_fusion_pass 0
  I-Fuse    scarab hpca2027-revision-ifuse (2e2b24240), LOAD2 dependents wake 1 cycle after LOAD1

Subcommands:
  package  copy <runs>/{base,ifuse}-{2,3}/<app>/ into
           scarab/src/hpca2027-revision/golden-cove-{baseline,ifuse-1cycle}-<P>-L1-D-read-ports/
           and write a summary.csv next to them (same columns as the 1-port summary.csv)
  plot     read the 1-port summaries from their scarab branches and the 2/3-port ones from
           the working tree, and write to scarab/src/hpca2027-revision/golden-cove-l1d-read-ports/:
             ifuse-speedup-same-port.{png,pdf}     I-Fuse over the baseline with the same ports
             speedup-over-1port-baseline.{png,pdf} every config over the 1-port baseline
             speedups.csv
           Average is the arithmetic mean of per-app speedups, as in plot_ipc.py.

Usage:
  golden_cove_l1d_read_ports.py package [--runs ~/hpca2027-revision-runs/golden-cove-l1d]
  golden_cove_l1d_read_ports.py plot
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))

SCARAB = Path.home() / "scarab"
RESULTS = "src/hpca2027-revision"
PORTS = (1, 2, 3)
# config -> (result dir stem, scarab ref holding the 1-port summary.csv)
CONFIGS = {
    "base": ("golden-cove-baseline", "origin/hpca2027-revision-ideal-fusion"),
    "ifuse": ("golden-cove-ifuse-1cycle", "origin/hpca2027-revision-ifuse"),
}
IFUSE_COUNTERS = (
    "IFUSE_FUSED_LOADS",
    "IFUSE_LOAD1_PREDICTIONS",
    "IFUSE_CORRECT_PREDICTIONS",
    "IFUSE_INCORRECT_PREDICTIONS",
    "IFUSE_MISPREDICTED_LOADS",
    "IFUSE_TRAINING_PAIRS_DISCOVERED",
)
APP_LABELS = {
    "deepsjeng_s": "deepsjeng", "exchange2_s": "exchange2", "gcc_s": "gcc-1", "gcc_s_2": "gcc-2",
    "gcc_s_3": "gcc-3", "leela_s": "leela", "mcf_s": "mcf", "omnetpp_s": "omnetpp",
    "perlbench_s": "perlbench-1", "perlbench_s_2": "perlbench-2", "perlbench_s_3": "perlbench-3",
    "xalancbmk_s": "xalancbmk", "xz_s": "xz-1", "xz_s_2": "xz-2",
}

# Stanford identity palette, one color per read-port count.
PORT_COLORS = {1: "#8C1515", 2: "#E98300", 3: "#4298B5"}
AXIS_FONT = 43
LEGEND_FONT = 40
TAG_FONT = 30
FIGSIZE = (30.0, 8.0)
BAR_EDGE_WIDTH = 3.0
SPINE_WIDTH = 2.5
AVERAGE_SEPARATOR_COLOR = "#2A2A2A"
AVERAGE_SEPARATOR_WIDTH = 3.5


def result_dir(cfg: str, ports: int) -> str:
    return f"{RESULTS}/{CONFIGS[cfg][0]}" + ("" if ports == 1 else f"-{ports}-L1-D-read-ports")


def summarize(run: Path, with_ifuse: bool) -> dict[str, str]:
    """Cycles, instructions and IPC after warmup, plus post-warmup I-Fuse counters."""
    text = (run / "core.stat.0.out").read_text()
    m = re.search(r"Periodic:\s+Cycles:\s+(\d+)\s+Instructions:\s+(\d+)\s+IPC:\s+([\d.]+)", text)
    if not m:
        raise SystemExit(f"no Periodic line in {run / 'core.stat.0.out'}")
    row = {"app": run.name, "cycles": m[1], "instructions": m[2], "ipc": m[3]}
    if with_ifuse:
        stats = {}
        for line in (run / "ifuse.stat.0.csv").read_text().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[0].endswith("_count") and not parts[0].endswith("_total_count"):
                stats[parts[0][: -len("_count")]] = parts[2]
        row.update({c: stats[c] for c in IFUSE_COUNTERS})
    return row


def package(args: argparse.Namespace) -> None:
    for cfg in CONFIGS:
        for ports in PORTS[1:]:
            src = args.runs / f"{cfg}-{ports}"
            out = args.scarab / result_dir(cfg, ports)
            rows = []
            for run in sorted(p for p in src.iterdir() if p.is_dir()):
                if not (run / "core.stat.0.out").exists() or "exit 0" not in (run / "sim.log").read_text():
                    raise SystemExit(f"incomplete run: {run}")
                dst = out / run.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(run, dst, ignore=shutil.ignore_patterns("PARAMS.in", "sim.log"))
                rows.append(summarize(run, cfg == "ifuse"))
            with (out / "summary.csv").open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(f"{out}: {len(rows)} apps")


def read_ipc(scarab: Path, cfg: str, ports: int) -> dict[str, float]:
    path = f"{result_dir(cfg, ports)}/summary.csv"
    if ports == 1:
        text = subprocess.run(["git", "-C", str(scarab), "show", f"{CONFIGS[cfg][1]}:{path}"],
                              check=True, capture_output=True, text=True).stdout
    else:
        text = (scarab / path).read_text()
    return {r["app"]: float(r["ipc"]) for r in csv.DictReader(io.StringIO(text))}


def draw(apps: list[str], bars: list[tuple[int, str, list[float]]], ylabel: str, legend_title: str,
         key: str | None, out: Path) -> None:
    """bars: (ports, tag, per-app speedup %) in left-to-right order within each app."""
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import matplotlib.transforms as transforms
    from matplotlib.patches import Patch

    import plot_ipc

    plot_ipc.register_noto_serif()
    font = plot_ipc.FONT_FAMILY
    plt.rcParams.update({"font.family": "serif", "font.serif": [font, "DejaVu Serif", "serif"]})
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.grid(True, axis="y", alpha=0.8, linestyle=":", color="black", linewidth=2.0, zorder=0)

    n = len(bars)
    width = 0.8 / n
    labels = [APP_LABELS.get(a, a) for a in apps] + ["Average"]
    centers = list(range(len(labels)))
    tick_x, tick_tags, values = [], [], []
    for i, (ports, tag, vals) in enumerate(bars):
        vals = vals + [sum(vals) / len(vals)]
        values += vals
        xs = [c + (i - (n - 1) / 2) * width for c in centers]
        tick_x += xs
        tick_tags += [tag] * len(xs)
        ax.bar(xs, vals, width, color=PORT_COLORS[ports], edgecolor="black",
               linewidth=BAR_EDGE_WIDTH, zorder=3)
    ax.axhline(0.0, color="black", linewidth=SPINE_WIDTH, zorder=4)
    ax.axvline(x=centers[-1] - 0.5, color=AVERAGE_SEPARATOR_COLOR, linestyle="--",
               linewidth=AVERAGE_SEPARATOR_WIDTH, zorder=2)

    below = transforms.blended_transform_factory(ax.transData, ax.transAxes)
    if any(tick_tags):
        # Each bar is tagged; app names sit below the tags.
        ax.set_xticks(tick_x)
        ax.set_xticklabels(tick_tags, fontsize=TAG_FONT)
        name_y = -0.11
    else:
        ax.set_xticks([])
        name_y = -0.03
    ax.tick_params(axis="x", length=0, pad=8)
    for c, name in zip(centers, labels):
        ax.text(c + 0.25, name_y, name, transform=below, rotation=45, ha="right", va="top",
                rotation_mode="anchor", fontsize=AXIS_FONT, weight="bold" if name == "Average" else "normal")
    ax.set_xlim(-0.5, centers[-1] + 0.5)

    lo, hi = min(min(values), 0.0), max(values)
    step = next(s for s in (1, 2, 5, 10, 20) if (hi - lo) / s <= 6)
    ax.set_ylim((lo // step) * step, (hi // step + 1) * step)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(step))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _p: f"{y:.0f}"))
    ax.tick_params(axis="y", labelsize=AXIS_FONT)
    ax.set_ylabel(ylabel, fontsize=AXIS_FONT, labelpad=20)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(SPINE_WIDTH)

    used = sorted({p for p, _, _ in bars})
    handles = [Patch(facecolor=PORT_COLORS[p], edgecolor="black", linewidth=BAR_EDGE_WIDTH,
                     label=f"{p} read port{'s' if p > 1 else ''}") for p in used]
    legend = ax.legend(handles=handles, title=legend_title, title_fontsize=LEGEND_FONT, frameon=True,
                       fancybox=False, loc="lower center", bbox_to_anchor=(0.5, 1.13 if key else 1.03),
                       fontsize=LEGEND_FONT, edgecolor="black", ncol=len(handles), handlelength=1.3,
                       handleheight=1.0, borderpad=0.5, columnspacing=1.2, framealpha=1.0)
    legend.get_frame().set_linewidth(BAR_EDGE_WIDTH)
    if key:
        ax.text(0.5, 1.03, key, transform=ax.transAxes, ha="center", va="bottom", fontsize=LEGEND_FONT)
    for ext in ("png", "pdf"):
        fig.savefig(out.with_suffix(f".{ext}"), bbox_inches="tight", pad_inches=0.08, dpi=300)
    plt.close(fig)


def plot(args: argparse.Namespace) -> None:
    ipc = {(cfg, p): read_ipc(args.scarab, cfg, p) for cfg in CONFIGS for p in PORTS}
    apps = list(ipc[("base", 1)])
    pct = lambda num, den: [(ipc[num][a] / ipc[den][a] - 1.0) * 100.0 for a in apps]
    mean = lambda v: sum(v) / len(v)

    same_port = [(p, "", pct(("ifuse", p), ("base", p))) for p in PORTS]
    over_1p = [(1, "I", pct(("ifuse", 1), ("base", 1)))]
    for p in PORTS[1:]:
        over_1p += [(p, "B", pct(("base", p), ("base", 1))), (p, "I", pct(("ifuse", p), ("base", 1)))]

    out = args.scarab / RESULTS / "golden-cove-l1d-read-ports"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "speedups.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["app"] + [f"ipc_{c}_{p}p" for c in CONFIGS for p in PORTS]
                   + [f"ifuse_over_base_{p}p_pct" for p in PORTS]
                   + [f"{c}_{p}p_over_base_1p_pct" for c in CONFIGS for p in PORTS])
        cols = ([ipc[(c, p)][a] for c in CONFIGS for p in PORTS] for a in apps)
        same = list(zip(*(v for _, _, v in same_port)))
        over = list(zip(*(pct((c, p), ("base", 1)) for c in CONFIGS for p in PORTS)))
        for a, ipcs, s, o in zip(apps, cols, same, over):
            w.writerow([a] + [f"{x:.5f}" for x in ipcs] + [f"{x:.2f}" for x in s] + [f"{x:.2f}" for x in o])
        w.writerow(["Average"] + [""] * 6 + [f"{mean(v):.2f}" for v in zip(*same)]
                   + [f"{mean(v):.2f}" for v in zip(*over)])

    draw(apps, same_port, "I-Fuse speedup\nover same-port\nbaseline (%)", "L1-D read ports", None,
         out / "ifuse-speedup-same-port")
    draw(apps, over_1p, "Speedup over\n1-port\nbaseline (%)", None,
         "B: Baseline      I: 1-cycle-delayed I-Fuse", out / "speedup-over-1port-baseline")

    print("Average speedup (%):")
    for p, _, v in same_port:
        print(f"  I-Fuse over {p}-port baseline: {mean(v):+.2f}")
    for p, tag, v in over_1p:
        print(f"  {'I-Fuse' if tag == 'I' else 'Baseline'} {p}-port over 1-port baseline: {mean(v):+.2f}")
    print(f"Wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("package", "plot"))
    ap.add_argument("--scarab", type=Path, default=SCARAB)
    ap.add_argument("--runs", type=Path, default=Path.home() / "hpca2027-revision-runs/golden-cove-l1d")
    args = ap.parse_args()
    package(args) if args.cmd == "package" else plot(args)


if __name__ == "__main__":
    main()
