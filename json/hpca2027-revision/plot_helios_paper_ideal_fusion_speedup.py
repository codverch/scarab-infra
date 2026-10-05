#!/usr/bin/env python3
"""Plot ideal-fusion speedup over no fusion on the Helios paper config.

Reads <results>/summary.csv (written by package_helios_paper_ideal_fusion_results.py)
and draws it with hpca2027-main-graphs/plot_ipc.py's plot_speedup_bars, so the
figure matches the main IPC graphs. Average is the arithmetic mean of the
per-app speedups, as in plot_ipc.py.

Writes <results>/speedup.{png,pdf} and speedup-labeled.{png,pdf}.

Usage: plot_helios_paper_ideal_fusion_speedup.py \
           [--results ~/scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "hpca2027-main-graphs"))
import plot_ipc  # noqa: E402

APP_LABELS = {
    "deepsjeng_s": "deepsjeng",
    "exchange2_s": "exchange2",
    "gcc_s": "gcc-1",
    "gcc_s_2": "gcc-2",
    "gcc_s_3": "gcc-3",
    "leela_s": "leela",
    "mcf_s": "mcf",
    "omnetpp_s": "omnetpp",
    "xalancbmk_s": "xalancbmk",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path,
                    default=Path.home() / "scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion")
    args = ap.parse_args()

    with open(args.results / "summary.csv") as f:
        rows = list(csv.DictReader(f))
    workloads = [r["app"] for r in rows]
    speedups = [float(r["speedup"]) for r in rows]

    plot_ipc.register_noto_serif()
    plot_ipc.rename_workload = lambda w: APP_LABELS.get(w, w)
    # plot_ipc sizes bars for four series per app; one series gets wider bars,
    # which also moves the Average column clear of the last app label.
    plot_ipc.BAR_WIDTH = 4.5
    plot_ipc.plot_speedup_bars(
        workloads,
        {"ideal_fusion": speedups},
        args.results,
        series=(("ideal_fusion", "Ideal fusion", plot_ipc.IDEAL_FUSION_COLOR),),
        file_prefix="speedup",
    )
    mean = sum(speedups) / len(speedups)
    print(f"Average speedup {(mean - 1) * 100:+.2f}%")
    print(f"Wrote {args.results}/speedup.{{png,pdf}}, speedup-labeled.{{png,pdf}}")


if __name__ == "__main__":
    main()
