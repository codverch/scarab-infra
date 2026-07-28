#!/usr/bin/env bash
# Resume Helios overnight from remaining apps (skip completed ones).
set -euo pipefail

INFRA=/users/deepmish/scarab-infra
SCARAB_SRC=/users/deepmish/scarab/src
RESULTS=/users/deepmish/helios-final-results
LOGDIR="$RESULTS/logs"
export MCPAT_BIN=/users/deepmish/scarab/src/toolchain/bin/mcpat
export CACTI_BIN=/users/deepmish/scarab/src/toolchain/bin/cacti
export SCARAB_KEEP_DOCKER_IMAGES=1
export PATH="$HOME/miniconda3/bin:$HOME/.local/bin:$PATH"

mkdir -p "$LOGDIR"
exec > >(tee -a "$LOGDIR/overnight.log") 2>&1

install_toolchain() {
  # IMPORTANT: binaries in /users/deepmish/toolchain/bin must be built INSIDE
  # allbench_traces (glibc 2.31). Do NOT overwrite with host-built mcpat/cacti
  # (host glibc 2.35) — that breaks McPAT inside containers.
  mkdir -p /users/deepmish/toolchain/bin "$(dirname "$MCPAT_BIN")"
  if [[ ! -x /users/deepmish/toolchain/bin/mcpat || ! -x /users/deepmish/toolchain/bin/cacti ]]; then
    echo "FATAL: missing container-compatible toolchain under /users/deepmish/toolchain/bin" >&2
    exit 1
  fi
  # Smoke-check: refuse host-glibc binaries if we can detect GLIBC_2.34 req via strings
  if strings /users/deepmish/toolchain/bin/mcpat | grep -q 'GLIBC_2\.34'; then
    echo "FATAL: mcpat requires GLIBC_2.34 — rebuild inside allbench_traces container" >&2
    exit 1
  fi
  cp -f /users/deepmish/toolchain/bin/mcpat "$MCPAT_BIN"
  cp -f /users/deepmish/toolchain/bin/cacti "$CACTI_BIN"
  chmod +x "$MCPAT_BIN" "$CACTI_BIN"
  for d in "$SCARAB_SRC"/scarab_stage/*/scarab/bin; do
    [[ -d "$d" ]] || continue
    cp -f "$MCPAT_BIN" "$CACTI_BIN" "$d/" || true
  done
  echo "[toolchain] container-compatible mcpat+cacti installed"
}

app_done() {
  local app="$1" root="$SCARAB_SRC/simulations/helios/$app"
  [[ -d "$root" ]] || return 1
  local sp total=0 ok=0
  shopt -s nullglob
  for sp in "$root"/*/; do
    [[ -d "$sp" ]] || continue
    # skip non-numeric cluster dirs
    [[ "$(basename "$sp")" =~ ^[0-9]+$ ]] || continue
    total=$((total+1))
    if [[ -s "${sp}core.stat.0.out" && -s "${sp}mcpat.out" ]]; then
      ok=$((ok+1))
    fi
  done
  shopt -u nullglob
  [[ "$total" -gt 0 && "$ok" -eq "$total" ]]
}

echo "===== RESUME $(date) ====="
install_toolchain

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate scarabinfra
cd "$INFRA"

# Kill any leftover image build
pkill -f 'sci --build-image allbench_traces' 2>/dev/null || true
pkill -f '\.sci-dockerfile-allbench_traces' 2>/dev/null || true
docker rm -f allbench_traces_deepmish_scarab_build 2>/dev/null || true

source ./json/hpca2027/helios.sh
register_traces
# Descriptor apps use underscores; HF dirs use hyphens. Sync DB + symlink layout.
python3 "$INFRA/json/hpca2027/fix_workload_aliases.py"
ensure_docker_image_tag
# Always re-tag in case cleaner removed the infra-hash tag
infra_hash="$(git -C "$INFRA" rev-parse --short HEAD)"
if ! docker image inspect "allbench_traces:${infra_hash}" >/dev/null 2>&1; then
  existing="$(docker images allbench_traces --format '{{.Tag}}' | head -1)"
  docker tag "allbench_traces:${existing}" "allbench_traces:${infra_hash}"
  echo "Retagged allbench_traces:${existing} -> ${infra_hash}"
fi

ensure_pinned_binary 0
install_toolchain

echo "=== Helios remaining apps ==="
print_config_table
for app in "${HELIOS_APPS[@]}"; do
  if app_done "$app"; then
    echo "SKIP $app (already complete with mcpat.out)"
    continue
  fi
  echo ">>> $app $(helios_label_for_app "$app")"
  # Re-ensure image tag every app (defense in depth)
  ensure_docker_image_tag
  write_helios_descriptor "$PINNED_BINARY" "$app"
  ./sci --sim hpca2027/helios
  install_toolchain
done
write_helios_descriptor "$PINNED_BINARY"

echo "===== Run baseline sims ====="
git -C /users/deepmish/scarab show cb2ec6957:src/PARAMS.golden_cove \
  > /users/deepmish/scarab/src/PARAMS.golden_cove || true
cp -f /users/deepmish/scarab/src/PARAMS.golden_cove \
  /users/deepmish/scarab/src/scarab_stage/baseline/scarab/ 2>/dev/null || true
ensure_docker_image_tag
install_toolchain
./sci --sim hpca2027/baseline 2>&1 | tee "$LOGDIR/baseline_sim.log"
install_toolchain

echo "===== Collect stats ====="
./sci --collect-stats hpca2027/baseline 2>&1 | tee "$LOGDIR/baseline_collect.log" || true
./sci --collect-stats hpca2027/helios 2>&1 | tee "$LOGDIR/helios_collect.log" || true

echo "===== Postprocess McPAT banks on any leftover XMLs ====="
python3 "$INFRA/json/hpca2027/fix_mcpat_banks_rerun.py" "$SCARAB_SRC/simulations" 2>&1 | tee "$LOGDIR/mcpat_postprocess.log" || true

echo "===== Finalize / tune ====="
python3 "$INFRA/json/hpca2027/helios_finalize.py" \
  --infra-dir "$INFRA" \
  --scarab-root "$SCARAB_SRC" \
  --results-dir "$RESULTS" \
  2>&1 | tee "$LOGDIR/finalize.log"

install_toolchain
echo "===== DONE $(date) ====="
