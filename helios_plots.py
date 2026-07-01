#!/usr/bin/env python3
"""Generate the two HELIOS reference graphs from an experiment run.

Driven entirely by json/HELIOS.json (no SUMMARY.csv / campaign scripts needed):
  * app -> run/config comes from helios_optimal.per_app_optimal[]
  * campaign root comes from runs[0].root_dir

Reads, per app, from a completed run (after `./sci --sim`/`--collect-stats` on each
HELIOS_T* group):
  <root>/simulations/<run>/collected_stats.csv
      -> weighted baseline & HELIOS IPC (per-app)  => benefit_latency.png
  <root>/simulations/<run>/<cfg>/helios_dc/helios_dc/<app>/<cid>/core.stat.0.out
      -> HELIOS_FUSION* candidate-outcome counters => fusion_breakdown.png

Writes <root>/benefit_latency.png and <root>/fusion_breakdown.png.

Usage (from the scarab-infra dir, after the run loop):
    python3 helios_plots.py            # uses ./json/HELIOS.json
    python3 helios_plots.py <HELIOS.json>
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import numpy as np
except ImportError as exc:  # pragma: no cover
    sys.exit(f"helios_plots.py needs matplotlib + numpy ({exc}). "
             f"Install into the run env, e.g. `pip install matplotlib numpy`.")

HERE = Path(__file__).resolve().parent
HELIOS_JSON = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "json" / "HELIOS.json"
WLDB_PATH = HERE / "workloads" / "workloads_db.json"

# Display order + short labels + fusion palette match the original reference plots.
DISPLAY_ORDER = [
    "bfs", "cc", "pagerank", "sssp_ego_fb", "tc", "bc", "dfs", "cd",
    "chemcrow", "rag_haystack", "langchain_web", "swe_agent", "toolformer",
    "mongodb", "mysql", "postgres",
]
LABEL_OVERRIDE = {"swe_agent": "swe", "rag_haystack": "haystack",
                  "langchain_web": "langchain", "sssp_ego_fb": "sssp"}
FUSION_CATEGORIES = [
    ("HELIOS_FUSIONS",                  "Helios fused",                   "#c92676"),
    ("HELIOS_REJECT_ADDR_MISMATCH",     "Unfused: Address mispredict",    "#6cb4d8"),
    ("HELIOS_REJECT_DISTANCE_INVALID",  "Unfused: Distance invalid",      "#8358a9"),
    ("HELIOS_REJECT_HEAD_EVICTED",      "Unfused: Head evicted (window)", "#f3c624"),
    ("HELIOS_REJECT_HEAD_ALREADY_FUSED","Unfused: Head already fused",    "#e88a32"),
    ("HELIOS_REJECT_NEST_LIMIT",        "Unfused: Nesting limit",         "#2f7d3f"),
    ("HELIOS_REJECT_DEADLOCK",          "Unfused: Register deadlock",     "#555555"),
    ("HELIOS_REJECT_SERIALIZING",       "Unfused: Serializing op",        "#b8b8b8"),
    ("HELIOS_REJECT_STORE_HAZARD",      "Unfused: Store hazard",          "#267d7d"),
]

WLDB = json.loads(WLDB_PATH.read_text())
HELIOS = json.loads(HELIOS_JSON.read_text())
PER_APP = {e["app"]: e for e in HELIOS["helios_optimal"]["per_app_optimal"]}
CAMPAIGN = Path(HELIOS["runs"][0]["root_dir"])
SIMS = CAMPAIGN / "simulations"


def app_simpoints(app: str) -> list[tuple[int, float]]:
    entry = WLDB["helios_dc"]["helios_dc"].get(app)
    return [(sp["cluster_id"], sp["weight"]) for sp in entry["simpoints"]] if entry else []


def parse_stat_file(path: Path, names: list[str]) -> dict[str, int]:
    out = {n: 0 for n in names}
    if not path.exists():
        return out
    needles = {n: re.compile(rf"^{re.escape(n)}\s+(\d+)") for n in names}
    for line in path.read_text().splitlines():
        s = line.strip()
        for n, rx in needles.items():
            m = rx.match(s)
            if m:
                out[n] = int(m.group(1))
                break
    return out


def weighted_ipc(run: str, cfg: str) -> dict[str, float]:
    """Weighted-avg IPC per app for one (run, config) from its collected_stats.csv."""
    p = SIMS / run / "collected_stats.csv"
    if not p.exists():
        return {}
    weights = {}
    for app, entry in WLDB["helios_dc"]["helios_dc"].items():
        if isinstance(entry, dict):
            for sp in entry.get("simpoints", []):
                weights[(app, sp["cluster_id"])] = sp["weight"]
    with p.open() as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return {}
        cols = []  # (col_idx, app, cid)
        for i, h in enumerate(header[3:], start=3):
            parts = h.split()
            if len(parts) != 3 or parts[0] != cfg:
                continue
            try:
                cols.append((i, parts[1].rsplit("/", 1)[-1], int(parts[2])))
            except ValueError:
                continue
        ipc_row = None
        for row in reader:
            if row and row[0] in ("IPC", "Cumulative_IPC", "Periodic_IPC"):
                ipc_row = row
                break
    if not cols or ipc_row is None:
        return {}
    acc, wsum = {}, {}
    for i, app, cid in cols:
        try:
            v = float(ipc_row[i])
        except (IndexError, ValueError):
            continue
        w = weights.get((app, cid), 1.0)
        acc[app] = acc.get(app, 0.0) + w * v
        wsum[app] = wsum.get(app, 0.0) + w
    return {a: acc[a] / wsum[a] for a in acc if wsum[a] > 0}


def app_speedup(app: str) -> tuple[float, bool]:
    """(% IPC vs baseline, beats_baseline). Recompute live from the run; fall back to
    the recorded per_app_optimal values if the run hasn't been executed yet."""
    e = PER_APP[app]
    run, cfg = e["run"], e["helios_config"]
    base = weighted_ipc(run, "baseline").get(app)
    hel = weighted_ipc(run, cfg).get(app)
    if base and hel:
        pct = (hel - base) / base * 100.0
    else:
        pct = e.get("speedup_pct") or 0.0
    return pct, not e["status"].startswith("under")


def app_fusion_breakdown(app: str) -> dict[str, float]:
    e = PER_APP[app]
    base = SIMS / e["run"] / e["helios_config"] / "helios_dc" / "helios_dc" / app
    if not base.is_dir():
        return {}
    names = [n for n, _, _ in FUSION_CATEGORIES]
    acc = {n: 0.0 for n in names}
    wsum = 0.0
    for cid, w in app_simpoints(app):
        stats = parse_stat_file(base / str(cid) / "core.stat.0.out", names)
        for n in names:
            acc[n] += w * stats[n]
        wsum += w
    return {n: acc[n] / wsum for n in names} if wsum else {}


def plot_benefit_latency(out_path: Path) -> None:
    apps = [a for a in DISPLAY_ORDER if a in PER_APP]
    data = [(a, *app_speedup(a)) for a in apps]
    labels = [LABEL_OVERRIDE.get(a, a) for a, _, _ in data]
    vals = [v for _, v, _ in data]
    colors = ["#4060c0" if beats else "#c0504d" for _, _, beats in data]

    fig, ax = plt.subplots(figsize=(13, 6))
    x = np.arange(len(data))
    bars = ax.bar(x, vals, color=colors, width=0.7)
    ax.legend(handles=[Patch(color="#4060c0", label="beats baseline"),
                       Patch(color="#c0504d", label="below baseline (best attempt)")],
              loc="upper right", frameon=False, fontsize=9)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_xlabel("Workloads"); ax.set_ylabel("IPC (% vs baseline)")
    ax.set_title("HELIOS IPC normalized to baseline — store-store fusion OFF")
    ax.yaxis.grid(True, linestyle="--", alpha=0.4); ax.set_axisbelow(True)
    ymax, ymin = max(vals + [0]), min(vals + [0])
    pad = max(abs(ymax), abs(ymin)) * 0.04 if (ymax or ymin) else 0.5
    for bar, v in zip(bars, vals):
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, h + (pad if h >= 0 else -pad),
                f"{v:.1f}", ha="center", va="bottom" if h >= 0 else "top", fontsize=9)
    ax.set_ylim(ymin - pad*4, ymax + pad*4)
    fig.tight_layout(); fig.savefig(out_path, dpi=140); plt.close(fig)
    print(f"Wrote {out_path}")


def plot_fusion_breakdown(out_path: Path) -> None:
    rows = []
    for app in DISPLAY_ORDER:
        if app not in PER_APP:
            continue
        stats = app_fusion_breakdown(app)
        if not stats:
            continue
        total = sum(stats.values())
        cats = {c[1]: (stats[c[0]] / total * 100.0 if total > 0 else 0.0)
                for c in FUSION_CATEGORIES}
        rows.append((app, cats))
    if not rows:
        print("fusion_breakdown: no core.stat data found — run the experiment first "
              "(./sci --sim on each HELIOS_T* group). Skipping.", file=sys.stderr)
        return
    rows.sort(key=lambda r: r[1]["Helios fused"], reverse=True)
    cats = [c[1] for c in FUSION_CATEGORIES]
    rows.append(("Average", {c: float(np.mean([r[1][c] for r in rows])) for c in cats}))

    labels = [LABEL_OVERRIDE.get(a, a) for a, _ in rows]
    n = len(rows); x = np.arange(n)
    colors = {c[1]: c[2] for c in FUSION_CATEGORIES}
    fig, ax = plt.subplots(figsize=(14, 5.8))
    fig.subplots_adjust(left=0.05, right=0.995, top=0.78, bottom=0.18)
    bottoms = np.zeros(n)
    for cat in cats:
        vals = np.array([r[1][cat] for r in rows])
        ax.bar(x, vals, bottom=bottoms, color=colors[cat], label=cat,
               width=0.86, edgecolor="white", linewidth=0.25)
        bottoms += vals
    ax.set_facecolor("#fafafa")
    ax.yaxis.grid(True, linestyle="-", linewidth=0.4, color="#cccccc")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#888"); ax.spines["bottom"].set_color("#888")
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=9)
    ax.get_xticklabels()[-1].set_fontweight("bold")
    ax.tick_params(axis="x", length=0, pad=2)
    ax.tick_params(axis="y", length=3, pad=2, labelsize=8.5)
    ax.set_xlim(-0.6, n - 0.4); ax.set_ylim(0, 100)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_ylabel("Breakdown of fusion candidates (%)", fontsize=9)
    ax.set_axisbelow(True)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.015), ncol=3, frameon=False,
              fontsize=8.5, handlelength=1.2, handleheight=1.0, columnspacing=1.6,
              labelspacing=0.4)
    fig.savefig(out_path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    if not SIMS.is_dir():
        print(f"NOTE: {SIMS} not found — plotting recorded values only; run the "
              f"experiment for live graphs.", file=sys.stderr)
    plot_benefit_latency(CAMPAIGN / "benefit_latency.png")
    plot_fusion_breakdown(CAMPAIGN / "fusion_breakdown.png")


if __name__ == "__main__":
    main()
