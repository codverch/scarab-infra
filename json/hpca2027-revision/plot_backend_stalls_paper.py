#!/usr/bin/env python3
"""Paper-style backend stall breakdown (RAT / ROB / LQ / SQ), ROB 352 vs 512.

Reuses the canvas, Helios colors and styling of
hpca2027-characterization/plot_backend_resource_stalls.py: two stacked bars
per app (ROB 352 solid, ROB 512 hatched), same attribution of allocation
stall cycles. Data comes from the packaged results
(<results>/rob-<N>/<app>/core.stat.0.csv).

Writes <results>/backend_stalls/backend-resource-stalls.{png,pdf}.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))
_spec = importlib.util.spec_from_file_location(
    "plot_backend_resource_stalls",
    INFRA / "hpca2027-characterization" / "plot_backend_resource_stalls.py",
)
brs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = brs  # dataclasses resolve types via sys.modules
_spec.loader.exec_module(brs)

APP_LABELS = {
    "gcc_s": "gcc-1",
    "gcc_s_2": "gcc-2",
    "gcc_s_3": "gcc-3",
    "leela_s": "leela",
    "mcf_s": "mcf",
    "omnetpp_s": "omnetpp",
    "xalancbmk_s": "xalancbmk",
}


def load_counts(path: Path) -> dict:
    counts = dict.fromkeys(brs.STATS, 0.0)
    for stat in brs.STATS:
        counts[stat] = float(brs.stat_count_from_csv(path, stat) or 0.0)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--apps", nargs="+", required=True)
    args = ap.parse_args()

    robs = sorted((p.name.split("-")[1] for p in args.results.glob("rob-*")), key=int)
    brs.CONFIGS = tuple(
        (f"rob{r}", f"ROB {r}", "" if i == 0 else "//") for i, r in enumerate(robs)
    )
    brs.BREAKDOWN_CATEGORIES.update(
        reg_file_pct="RAT", rob_pct="ROB", lq_pct="LQ", sq_pct="SQ"
    )
    brs.display_name = lambda w: "Average" if w == "Average" else APP_LABELS.get(w, w)
    brs.Y_MAX = 90.0
    brs.OUTPUT_STEM = "backend-resource-stalls"

    results = []
    for app in args.apps:
        breakdowns = {}
        for r in robs:
            b = brs.breakdown_from_counts(load_counts(args.results / f"rob-{r}" / app / "core.stat.0.csv"))
            if b is None:
                raise SystemExit(f"no cycles for rob-{r}/{app}")
            breakdowns[f"rob{r}"] = b
        results.append(brs.WorkloadResult(workload=app, trace_count=1, breakdowns=breakdowns))

    brs.register_noto_serif()
    out = args.results / "backend_stalls"
    brs.plot_breakdown(results, out)
    print(f"Wrote {out}/{brs.OUTPUT_STEM}.{{png,pdf}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
