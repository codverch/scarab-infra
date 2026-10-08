#!/usr/bin/env bash
# Regenerate the Helios coverage breakdown (paper Fig. 4) from committed results.
#
# Data sources (scarab repo branches):
#   Helios runs:       hpca2027-main            src/simulations/helios
#   Ideal fusion runs: hpca2027-ideal-fusion    src/simulations/ideal-fusion
#                      (bfs-init -> bfs, dfs-init -> dfs, pagerank-gnutella31 -> pagerank)
#   CacheBench ideal:  hpca2027-revision-main   .../datacenter/ideal-fusion/cachebench
# All runs: 1 L1-D read port, 30M instructions, 20M warmup.
# SimPoint weights come from workloads/workloads_db.json (Memcached uses the
# trace release weights).
#
# Usage:
#   regen_helios_coverage_causes.sh [OUTPUT_DIR] [PAPER_RESULTS_DIR]
# OUTPUT_DIR defaults to scarab/src/hpca2027-characterization-results/helios-coverage-causes.
# If PAPER_RESULTS_DIR is given, the PDF is copied there as helios-coverage-causes.pdf.

set -euo pipefail

SCARAB=${SCARAB:-/users/deepmish/scarab}
PYTHON=${PYTHON:-/users/deepmish/miniconda3/envs/scarabinfra/bin/python}
TRACE_ROOT=${TRACE_ROOT:-/dev/shm/ifuse}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$SCARAB/src/hpca2027-characterization-results/helios-coverage-causes}
PAPER=${2:-}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

git -C "$SCARAB" fetch -q origin hpca2027-main hpca2027-ideal-fusion hpca2027-revision-main

git -C "$SCARAB" archive origin/hpca2027-main src/simulations/helios | tar -x -C "$WORK"
mkdir -p "$WORK/helios-src" "$WORK/ideal"
git -C "$SCARAB" archive origin/hpca2027-ideal-fusion src/simulations/ideal-fusion \
  | tar -x -C "$WORK/helios-src"
for pair in bfs-init:bfs dfs-init:dfs pagerank-gnutella31:pagerank appworld:appworld \
            clickhouse:clickhouse corebench:corebench duckdb:duckdb leveldb:leveldb \
            memcached:memcached terminal_bench:terminal_bench; do
  mv "$WORK/helios-src/src/simulations/ideal-fusion/${pair%%:*}" "$WORK/ideal/${pair##*:}"
done
git -C "$SCARAB" archive origin/hpca2027-revision-main \
  src/hpca2027-revision-main-results/datacenter/ideal-fusion/cachebench | tar -x -C "$WORK"
mv "$WORK/src/hpca2027-revision-main-results/datacenter/ideal-fusion/cachebench" "$WORK/ideal/"

"$PYTHON" "$HERE/plot_helios_coverage_causes.py" \
  --simulations-root "$WORK/src/simulations" \
  --helios-dir "$WORK/src/simulations/helios" \
  --ideal-fusion-dir "$WORK/ideal" \
  --trace-root "$TRACE_ROOT" \
  --output-dir "$OUT"

if [[ -n "$PAPER" ]]; then
  cp "$OUT/helios-coverage-causes.pdf" "$PAPER/helios-coverage-causes.pdf"
  echo "Copied PDF to $PAPER"
fi
