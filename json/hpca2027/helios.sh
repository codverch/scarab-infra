#!/usr/bin/env bash
# Helios: per-app knobs, reuse one Scarab binary (no rebuild per app)
# =============================================================================
# Notation: T=threshold  W=window  I=increment  D=decrement  stores-on/off
#
# Per-app knobs (stores-off for all):
#   Graph / CRONO apps  T300/W64/I1/D10/stores-off
#   haystack            T1000/W64/I1/D10/stores-off
#   leveldb             T4800/W64/I1/D10/stores-off
#
# Efficiency: pin descriptor binary to a hash-named cache entry so each
# ./sci --sim skips rebuild_scarab (which fires on scarab_current + dirty tree).
# Docker image is retagged once if the infra-hash tag is missing.
#
# Usage:
#   ./helios.sh              # register + per-app sim (no rebuild)
#   ./helios.sh --build      # rebuild Scarab once, then run
#   ./helios.sh --dry-run
#   ./helios.sh --status
#   ./helios.sh --collect-stats

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESCRIPTOR="hpca2027/helios"
DESCRIPTOR_JSON="${INFRA_DIR}/json/hpca2027/helios.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
SCARAB_PATH="/users/deepmish/scarab"
HELIOS_GIT_REF="${HELIOS_GIT_REF:-hpca2027-helios}"
BUILDS_DIR="${INFRA_DIR}/scarab_builds"
export MCPAT_BIN="${MCPAT_BIN:-/users/deepmish/scarab/src/toolchain/bin/mcpat}"
export CACTI_BIN="${CACTI_BIN:-/users/deepmish/scarab/src/toolchain/bin/cacti}"
WARMUP=20000000
INST_LIMIT=30000000

checkout_helios_scarab() {
  git -C "${SCARAB_PATH}" checkout -f "${HELIOS_GIT_REF}"
  git -C "${SCARAB_PATH}" reset --hard "${HELIOS_GIT_REF}"
  local patch=/users/deepmish/helios-final-results/patches/mcpat_banks_power_softfail.patch
  if [[ -f "${patch}" ]]; then
    git -C "${SCARAB_PATH}" apply --whitespace=nowarn "${patch}" || \
      git -C "${SCARAB_PATH}" apply --reverse --check "${patch}" 2>/dev/null || \
      echo "WARN: could not apply ${patch}" >&2
  fi
  echo "Scarab at Helios ref: $(git -C "${SCARAB_PATH}" rev-parse --short HEAD)"
  mkdir -p "$(dirname "${MCPAT_BIN}")"
  if [[ -x /users/deepmish/toolchain/bin/mcpat ]]; then
    cp -f /users/deepmish/toolchain/bin/mcpat "${MCPAT_BIN}"
    cp -f /users/deepmish/toolchain/bin/cacti "${CACTI_BIN}"
  fi
}

HELIOS_APPS=(
  appworld bfs-init bfs-web-google clickhouse corebench dfs-init dfs-web-google
  duckdb grpc leveldb memcached pagerank-gnutella31 pagerank-init rocksdb
  sqlite sssp-ego-facebook sssp-init terminal_bench
)

# Workload / result dirs match simpoint_traces names 1:1.
trace_dir_for_app() {
  echo "$1"
}

declare -A HELIOS_T=(
  [appworld]=10000 [bfs-init]=300 [bfs-web-google]=300 [clickhouse]=300
  [corebench]=1000 [dfs-init]=300 [dfs-web-google]=300 [duckdb]=30000
  [grpc]=300 [leveldb]=4800 [memcached]=4800 [pagerank-gnutella31]=300
  [pagerank-init]=300 [rocksdb]=4800 [sqlite]=30000 [sssp-ego-facebook]=300
  [sssp-init]=300 [terminal_bench]=100
)
declare -A HELIOS_W=(
  [appworld]=64 [bfs-init]=64 [bfs-web-google]=64 [clickhouse]=64
  [corebench]=64 [dfs-init]=64 [dfs-web-google]=64 [duckdb]=64 [grpc]=64
  [leveldb]=64 [memcached]=64 [pagerank-gnutella31]=64 [pagerank-init]=64
  [rocksdb]=64 [sqlite]=64 [sssp-ego-facebook]=64 [sssp-init]=64
  [terminal_bench]=64
)
declare -A HELIOS_I=(
  [appworld]=1 [bfs-init]=1 [bfs-web-google]=1 [clickhouse]=10 [corebench]=1
  [dfs-init]=1 [dfs-web-google]=1 [duckdb]=1 [grpc]=10 [leveldb]=1
  [memcached]=1 [pagerank-gnutella31]=1 [pagerank-init]=1 [rocksdb]=1
  [sqlite]=1 [sssp-ego-facebook]=1 [sssp-init]=1 [terminal_bench]=1
)
declare -A HELIOS_D=(
  [appworld]=10 [bfs-init]=10 [bfs-web-google]=10 [clickhouse]=10
  [corebench]=10 [dfs-init]=10 [dfs-web-google]=10 [duckdb]=10 [grpc]=10
  [leveldb]=10 [memcached]=10 [pagerank-gnutella31]=10 [pagerank-init]=10
  [rocksdb]=10 [sqlite]=10 [sssp-ego-facebook]=10 [sssp-init]=10
  [terminal_bench]=10
)
# stores-off for every app
declare -A HELIOS_STORES=(
  [appworld]=0 [bfs-init]=0 [bfs-web-google]=0 [clickhouse]=0 [corebench]=0
  [dfs-init]=0 [dfs-web-google]=0 [duckdb]=0 [grpc]=0 [leveldb]=0
  [memcached]=0 [pagerank-gnutella31]=0 [pagerank-init]=0 [rocksdb]=0
  [sqlite]=0 [sssp-ego-facebook]=0 [sssp-init]=0 [terminal_bench]=0
)
# 1=enable Helios fusion; 0=force off (when fusion hurts vs baseline)
declare -A HELIOS_DO_FUSION=(
  [appworld]=1 [bfs-init]=1 [bfs-web-google]=1 [clickhouse]=1 [corebench]=1
  [dfs-init]=1 [dfs-web-google]=0 [duckdb]=1 [grpc]=1 [leveldb]=0
  [memcached]=1 [pagerank-gnutella31]=1 [pagerank-init]=1 [rocksdb]=1
  [sqlite]=1 [sssp-ego-facebook]=1 [sssp-init]=1 [terminal_bench]=1
)

helios_label_for_app() {
  local app="$1"
  local fus=on; [[ ${HELIOS_DO_FUSION[$app]} -eq 0 ]] && fus=off
  printf 'T%s/W%s/I%s/D%s/stores-off/fusion-%s' \
    "${HELIOS_T[$app]}" "${HELIOS_W[$app]}" "${HELIOS_I[$app]}" "${HELIOS_D[$app]}" "$fus"
}

helios_knobs_for_app() {
  local app="$1"
  printf '%s' \
    "--helios_do_fusion ${HELIOS_DO_FUSION[$app]} --helios_enable_flushes 1 " \
    "--helios_confidence_threshold ${HELIOS_T[$app]} " \
    "--helios_confidence_increment ${HELIOS_I[$app]} " \
    "--helios_confidence_decrement ${HELIOS_D[$app]} " \
    "--helios_fusion_window ${HELIOS_W[$app]} " \
    "--helios_fuse_stores ${HELIOS_STORES[$app]} " \
    "--helios_fused_wait_tail_srcs 1 " \
    "--helios_extended_commit_group 1"
}

print_config_table() {
  local app
  printf '%-16s  %s\n' "app" "config"
  printf '%-16s  %s\n' "----------------" "---------------------------"
  for app in "${HELIOS_APPS[@]}"; do
    printf '%-16s  %s\n' "${app}" "$(helios_label_for_app "${app}")"
  done
}

# Prefer an existing hash-pinned cache binary so sci never enters rebuild_scarab.
resolve_pinned_binary() {
  local link target base
  link="${BUILDS_DIR}/scarab_current.opt"
  if [[ -L "${link}" ]]; then
    target="$(readlink "${link}")"
    base="${target%.opt}"
    base="${base%.dbg}"
    if [[ -f "${BUILDS_DIR}/${target}" || -f "${BUILDS_DIR}/${base}.opt" ]]; then
      printf '%s' "${base}"
      return 0
    fi
  fi
  # Fall back to any non-empty scarab_*_0.opt
  local f
  for f in "${BUILDS_DIR}"/scarab_*_0.opt; do
    [[ -f "$f" && -s "$f" ]] || continue
    base="$(basename "$f" .opt)"
    printf '%s' "${base}"
    return 0
  done
  return 1
}

ensure_docker_image_tag() {
  local infra_hash img existing
  infra_hash="$(git -C "${INFRA_DIR}" rev-parse --short HEAD)"
  img="allbench_traces:${infra_hash}"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    echo "Docker image ready: ${img}"
    return 0
  fi
  existing="$(docker images allbench_traces --format '{{.Tag}}' | head -1 || true)"
  if [[ -z "${existing}" ]]; then
    echo "ERROR: no allbench_traces image to retag; run: ./sci --build-image allbench_traces" >&2
    return 1
  fi
  echo "Retagging allbench_traces:${existing} -> ${img} (skip full rebuild)"
  docker tag "allbench_traces:${existing}" "${img}"
}

# write_helios_descriptor <binary_name> [app]
# If app is set: single-workload + that app's knobs. Else: all apps + placeholder knobs.
write_helios_descriptor() {
  local binary="$1"
  local app="${2:-}"
  local knobs comment
  local -a apps_for_json=()

  if [[ -n "${app}" ]]; then
    apps_for_json=("${app}")
    knobs="$(helios_knobs_for_app "${app}")"
    comment="Helios per-app: ${app} $(helios_label_for_app "${app}"). 50M warmup + 20M sim. binary=${binary}."
  else
    apps_for_json=("${HELIOS_APPS[@]}")
    knobs="$(helios_knobs_for_app "terminal_bench")"
    comment="Helios per-app knobs (see helios.sh). binary=${binary}. stores-off for all. architecture=in (PARAMS.in)."
  fi

  local per_app_blob="" a
  for a in "${HELIOS_APPS[@]}"; do
    per_app_blob+="${a}=$(helios_label_for_app "${a}")"$'\n'
  done

  python3 - "${DESCRIPTOR_JSON}" "${binary}" "${knobs}" "${comment}" "${WARMUP}" "${INST_LIMIT}" \
    "$(printf '%s\n' "${apps_for_json[@]}")" "${per_app_blob}" <<'PY'
import json, sys
from pathlib import Path

desc_path = Path(sys.argv[1])
binary = sys.argv[2]
knobs = sys.argv[3]
comment = sys.argv[4]
warmup = int(sys.argv[5])
inst_limit = int(sys.argv[6])
workloads = [w for w in sys.argv[7].splitlines() if w]
per_app = {}
for line in sys.argv[8].splitlines():
    if not line or "=" not in line:
        continue
    k, v = line.split("=", 1)
    per_app[k] = v

desc = json.loads(desc_path.read_text())
desc["architecture"] = "in"
experiment = desc["experiment"]
common = (
    f"--icache_size 32768 --inst_limit {inst_limit} "
    f"--full_warmup {warmup} --power_intf_on 1 "
    f"--bindir {{root_dir}}/scarab_stage/{experiment}/scarab/bin"
)
desc["_comment"] = comment
desc["helios_per_app"] = per_app
desc["simulations"][0]["workload"] = workloads
desc["simulations"][0]["warmup"] = warmup
desc["configurations"]["helios"] = {
    "params": f"{common} {knobs}".strip(),
    "binary": binary,
    "slurm_options": "",
    "memory_overhead_mb": 0,
}
desc_path.write_text(json.dumps(desc, indent=2) + "\n")
print(f"Updated {desc_path} workloads={workloads}")
print(f"  binary: {binary}")
print(f"  params: {desc['configurations']['helios']['params']}")
PY
}

discover_present_apps() {
  local app trace_dir
  PRESENT_APPS=()
  for app in "${HELIOS_APPS[@]}"; do
    trace_dir="$(trace_dir_for_app "${app}")"
    if [[ -d "${TRACES_DIR}/${trace_dir}" ]] && \
       find "${TRACES_DIR}/${trace_dir}" -path '*/traces_simp/*' -name '*.zip' 2>/dev/null | grep -q .; then
      PRESENT_APPS+=("${app}")
    else
      echo "SKIP ${app}: no traces under ${TRACES_DIR}/${trace_dir}" >&2
    fi
  done
}

register_traces() {
  discover_present_apps
  if [[ ${#PRESENT_APPS[@]} -eq 0 ]]; then
    echo "ERROR: no Helios apps with traces under ${TRACES_DIR}" >&2
    exit 1
  fi
  local app trace_dirs=()
  for app in "${PRESENT_APPS[@]}"; do
    trace_dirs+=("$(trace_dir_for_app "${app}")")
  done
  echo "Registering ${#PRESENT_APPS[@]} workload(s): ${PRESENT_APPS[*]}"
  python3 -m scripts.register_local_traces \
    --traces-dir "${TRACES_DIR}" \
    --workloads "${trace_dirs[@]}" \
    --warmup "${WARMUP}"
}

ensure_pinned_binary() {
  local do_build="${1:-0}"
  local pinned helios_hash

  checkout_helios_scarab
  helios_hash="$(git -C "${SCARAB_PATH}" rev-parse --short HEAD)"
  find "${BUILDS_DIR}" -maxdepth 1 -type f -name 'scarab_*' -size 0 -delete 2>/dev/null || true

  if [[ "${do_build}" == "1" ]]; then
    write_helios_descriptor "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
    checkout_helios_scarab
  fi

  if ! pinned="$(resolve_pinned_binary)"; then
    echo "No cached Scarab binary in ${BUILDS_DIR}; building once..."
    write_helios_descriptor "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
    checkout_helios_scarab
    pinned="$(resolve_pinned_binary)" || {
      echo "ERROR: still no cached binary after build" >&2
      exit 1
    }
  fi

  if [[ ! -s "${BUILDS_DIR}/${pinned}.opt" && ! -s "${BUILDS_DIR}/${pinned}" ]]; then
    echo "ERROR: pinned binary missing/empty: ${pinned}" >&2
    exit 1
  fi
  if [[ "${pinned}" != *"${helios_hash}"* ]]; then
    echo "WARN: pinned ${pinned} does not match Helios hash ${helios_hash}; rebuilding" >&2
    write_helios_descriptor "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
    checkout_helios_scarab
    pinned="$(resolve_pinned_binary)" || true
  fi
  echo "Using pinned Scarab binary: ${pinned} (Helios ${helios_hash}, no per-app rebuild)"
  PINNED_BINARY="${pinned}"
}

run_sim() {
  local app
  register_traces
  ensure_docker_image_tag
  ensure_pinned_binary 0

  echo ""
  echo "=== Helios per-app runs (warmup=${WARMUP}, inst_limit=${INST_LIMIT}) ==="
  print_config_table
  echo ""

  for app in "${HELIOS_APPS[@]}"; do
    local found=0 w
    for w in "${PRESENT_APPS[@]}"; do
      [[ "${w}" == "${app}" ]] && found=1 && break
    done
    if [[ "${found}" -ne 1 ]]; then
      continue
    fi
    echo ">>> ${app}  $(helios_label_for_app "${app}")"
    write_helios_descriptor "${PINNED_BINARY}" "${app}"
    ./sci --sim "${DESCRIPTOR}"
  done

  write_helios_descriptor "${PINNED_BINARY}"
  if [[ "${SKIP_COLLECT_STATS:-0}" != "1" ]]; then
    ./sci --collect-stats "${DESCRIPTOR}" || true
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [--build|--dry-run|--status|--collect-stats|--help]

Per-app Helios configs (stores-off for all):
EOF
  print_config_table
}

main() {
  cd "${INFRA_DIR}"
  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi

  case "${1:-}" in
    ""|--all|--sim-only)
      run_sim
      ;;
    --build)
      # Build once, then run_sim (which reuses the pinned binary — no rebuild).
      ensure_docker_image_tag
      ensure_pinned_binary 1
      run_sim
      ;;
    --dry-run)
      discover_present_apps
      echo "Present: ${PRESENT_APPS[*]:-none}"
      print_config_table
      if pinned="$(resolve_pinned_binary 2>/dev/null)"; then
        echo "Would pin binary: ${pinned}"
        write_helios_descriptor "${pinned}"
      else
        echo "No pinned binary yet (would build once on --build / first run)"
        write_helios_descriptor "scarab_current"
      fi
      ;;
    --status)
      ./sci --status "${DESCRIPTOR}"
      ;;
    --collect-stats)
      ./sci --collect-stats "${DESCRIPTOR}"
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
