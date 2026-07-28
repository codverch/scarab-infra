#!/usr/bin/env bash
set -euo pipefail

INFRA=/users/deepmish/scarab-infra
SCARAB_SRC=/users/deepmish/scarab/src
RESULTS=/users/deepmish/helios-final-results
LOGDIR="$RESULTS/logs"
TOOLCHAIN_HOST=/users/deepmish/toolchain/bin
export MCPAT_BIN=/users/deepmish/scarab/src/toolchain/bin/mcpat
export CACTI_BIN=/users/deepmish/scarab/src/toolchain/bin/cacti
export PATH="$HOME/miniconda3/bin:$HOME/.local/bin:$PATH"
export SCARAB_KEEP_DOCKER_IMAGES=1

mkdir -p "$LOGDIR" "$TOOLCHAIN_HOST" "$(dirname "$MCPAT_BIN")"
exec > >(tee -a "$LOGDIR/overnight.log") 2>&1

install_toolchain() {
  # Keep container-built (glibc 2.31) binaries; never overwrite from host mcpat tree.
  mkdir -p "$TOOLCHAIN_HOST" "$(dirname "$MCPAT_BIN")"
  if [[ ! -x /users/deepmish/toolchain/bin/mcpat || ! -x /users/deepmish/toolchain/bin/cacti ]]; then
    echo "FATAL: missing /users/deepmish/toolchain/bin/{mcpat,cacti}" >&2
    exit 1
  fi
  if strings /users/deepmish/toolchain/bin/mcpat | grep -q 'GLIBC_2\.34'; then
    echo "FATAL: host-glibc mcpat detected; rebuild inside allbench_traces" >&2
    exit 1
  fi
  cp -f /users/deepmish/toolchain/bin/mcpat "$MCPAT_BIN"
  cp -f /users/deepmish/toolchain/bin/cacti "$CACTI_BIN"
  chmod +x "$MCPAT_BIN" "$CACTI_BIN" "$TOOLCHAIN_HOST"/*
  for d in "$SCARAB_SRC"/scarab_stage/*/scarab/bin; do
    [[ -d "$d" ]] || continue
    cp -f "$MCPAT_BIN" "$CACTI_BIN" "$d/" || true
  done
  echo "[toolchain] $MCPAT_BIN ready (container/Helios+RFP)"
}

echo "===== START $(date) ====="
install_toolchain

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate scarabinfra
cd "$INFRA"
docker rm -f allbench_traces_deepmish_scarab_build 2>/dev/null || true

# Fresh simulation trees for the expanded app set
rm -rf "$SCARAB_SRC/simulations/helios" "$SCARAB_SRC/simulations/baseline"
mkdir -p "$SCARAB_SRC/simulations/helios" "$SCARAB_SRC/simulations/baseline"

source ./json/hpca2027/helios.sh
register_traces
ensure_docker_image_tag

# Baseline binary
if [[ ! -s scarab_builds/scarab_cb2ec6957.opt && ! -s scarab_builds/scarab_cb2ec6957 ]]; then
  echo "===== Build baseline Scarab ====="
  ./sci --build-scarab hpca2027/baseline 2>&1 | tee "$LOGDIR/build_baseline.log"
  install_toolchain
else
  echo "===== Baseline binary present; skip build ====="
  ls -la scarab_builds/scarab_cb2ec6957* || true
fi

# Helios binary on hpca2027-helios
echo "===== Ensure Helios Scarab binary ====="
ensure_pinned_binary 0
install_toolchain

echo "===== Run Helios sims (per-app) ====="
export SKIP_COLLECT_STATS=1
run_sim 2>&1 | tee "$LOGDIR/helios_run.log"
install_toolchain

echo "===== Run baseline sims ====="
# Helios branch lacks PARAMS.golden_cove; restore from baseline commit for baseline runs.
git -C /users/deepmish/scarab show cb2ec6957:src/PARAMS.golden_cove \
  > /users/deepmish/scarab/src/PARAMS.golden_cove || true
cp -f /users/deepmish/scarab/src/PARAMS.golden_cove \
  /users/deepmish/scarab/src/scarab_stage/baseline/scarab/ 2>/dev/null || true
install_toolchain
./sci --sim hpca2027/baseline 2>&1 | tee "$LOGDIR/baseline_sim.log"
install_toolchain

echo "===== Collect stats ====="
./sci --collect-stats hpca2027/baseline 2>&1 | tee "$LOGDIR/baseline_collect.log" || true
./sci --collect-stats hpca2027/helios 2>&1 | tee "$LOGDIR/helios_collect.log" || true

echo "===== Finalize / tune ====="
python3 "$INFRA/json/hpca2027/helios_finalize.py" \
  --infra-dir "$INFRA" \
  --scarab-root "$SCARAB_SRC" \
  --results-dir "$RESULTS" \
  2>&1 | tee "$LOGDIR/finalize.log"

install_toolchain

# Persist tuned configs back to git
echo "===== Commit + push tuned Helios configs ====="
cd "$INFRA"
git add json/hpca2027/helios.json json/hpca2027/helios.sh json/hpca2027/baseline.json \
  json/hpca2027/helios_finalize.py json/hpca2027/package_helios_results.py \
  json/hpca2027/tune_helios_speedup.py json/hpca2027/verify_helios_results.py \
  json/hpca2027/overnight_helios.sh json/hpca2027/resume_helios.sh \
  json/hpca2027/run_helios_final.sh json/hpca2027/fix_mcpat_banks_rerun.py \
  workloads/workloads_db.json workloads/allbench_traces/workload_user_entrypoint.sh \
  scripts/docker_cleaner.py \
  2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "$(cat <<'EOF'
Update Helios configs after positive-speedup tune across ifuse-traces apps.

EOF
)" || true
  git push origin HEAD || echo "WARN: push failed"
else
  echo "No tuned config changes to commit"
fi

echo "===== DONE $(date) ====="
