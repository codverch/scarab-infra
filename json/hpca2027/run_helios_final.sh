#!/usr/bin/env bash
# End-to-end Helios experiment: baseline + per-app helios, IPC check, mcpat verify, results bundle.
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULTS_DIR="${INFRA_DIR}/hpca2027-helios-results"
LOG_DIR="${RESULTS_DIR}/logs"
export MCPAT_BIN="${MCPAT_BIN:-/users/deepmish/mcpat/mcpat}"
export CACTI_BIN="${CACTI_BIN:-/users/deepmish/mcpat/cacti/cacti}"

mkdir -p "$LOG_DIR"

if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "${HOME}/miniconda3/etc/profile.d/conda.sh"
  conda activate scarabinfra 2>/dev/null || true
fi

cd "$INFRA_DIR"

echo "=== McPAT/CACTI ==="
[[ -x "$MCPAT_BIN" && -x "$CACTI_BIN" ]] || { echo "ERROR: set MCPAT_BIN/CACTI_BIN"; exit 1; }

echo "=== Register traces ==="
python3 -m scripts.register_local_traces \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --warmup 20000000 \
  --workloads appworld bfs-web-google clickhouse corebench dfs-web-google duckdb \
            leveldb pagerank-gnutella31 rocksdb sssp-ego-facebook terminal_bench

echo "=== Build baseline Scarab ==="
./sci --build-scarab hpca2027/baseline 2>&1 | tee "$LOG_DIR/build_baseline.log"

echo "=== Build + run Helios (per-app knobs) ==="
./json/hpca2027/helios.sh --build 2>&1 | tee "$LOG_DIR/helios_build_run.log"

echo "=== Run baseline sims ==="
./sci --sim hpca2027/baseline 2>&1 | tee "$LOG_DIR/baseline_sim.log"

echo "=== Collect stats ==="
./sci --collect-stats hpca2027/baseline 2>&1 | tee "$LOG_DIR/baseline_collect.log" || true
./sci --collect-stats hpca2027/helios 2>&1 | tee "$LOG_DIR/helios_collect.log" || true

echo "=== IPC + mcpat verification ==="
python3 "${INFRA_DIR}/json/hpca2027/verify_helios_results.py" \
  --infra-dir "$INFRA_DIR" \
  --results-dir "$RESULTS_DIR" \
  --scarab-root /users/deepmish/scarab/src

echo "Done. See ${RESULTS_DIR}/README.md"
