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
BUILDS_DIR="${INFRA_DIR}/scarab_builds"
WARMUP=50000000
INST_LIMIT=70000000

HELIOS_APPS=(
  apsp_synth bc_synth bfs_web-google community_web_google connected_components_web_google dfs_web-google
  haystack_rag leveldb_ycsb pagerank_web-google sssp_ego-facebook triangle_counting_web_google
)

declare -A HELIOS_T=(
  [apsp_synth]=300 [bc_synth]=300 [bfs_web-google]=300 [community_web_google]=300
  [connected_components_web_google]=300 [dfs_web-google]=300 [haystack_rag]=1000
  [leveldb_ycsb]=4800 [pagerank_web-google]=300 [sssp_ego-facebook]=300
  [triangle_counting_web_google]=300
)
declare -A HELIOS_W=(
  [apsp_synth]=64 [bc_synth]=64 [bfs_web-google]=64 [community_web_google]=64
  [connected_components_web_google]=64 [dfs_web-google]=64 [haystack_rag]=64
  [leveldb_ycsb]=64 [pagerank_web-google]=64 [sssp_ego-facebook]=64
  [triangle_counting_web_google]=64
)
declare -A HELIOS_I=(
  [apsp_synth]=1 [bc_synth]=1 [bfs_web-google]=1 [community_web_google]=1
  [connected_components_web_google]=1 [dfs_web-google]=1 [haystack_rag]=1
  [leveldb_ycsb]=1 [pagerank_web-google]=1 [sssp_ego-facebook]=1
  [triangle_counting_web_google]=1
)
declare -A HELIOS_D=(
  [apsp_synth]=10 [bc_synth]=10 [bfs_web-google]=10 [community_web_google]=10
  [connected_components_web_google]=10 [dfs_web-google]=10 [haystack_rag]=10
  [leveldb_ycsb]=10 [pagerank_web-google]=10 [sssp_ego-facebook]=10
  [triangle_counting_web_google]=10
)
# stores-off for every app
declare -A HELIOS_STORES=(
  [apsp_synth]=0 [bc_synth]=0 [bfs_web-google]=0 [community_web_google]=0
  [connected_components_web_google]=0 [dfs_web-google]=0 [haystack_rag]=0
  [leveldb_ycsb]=0 [pagerank_web-google]=0 [sssp_ego-facebook]=0
  [triangle_counting_web_google]=0
)

helios_label_for_app() {
  local app="$1"
  printf 'T%s/W%s/I%s/D%s/stores-off' \
    "${HELIOS_T[$app]}" "${HELIOS_W[$app]}" "${HELIOS_I[$app]}" "${HELIOS_D[$app]}"
}

helios_knobs_for_app() {
  local app="$1"
  printf '%s' \
    "--helios_do_fusion 1 --helios_enable_flushes 1 " \
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

  python3 - "${DESCRIPTOR_JSON}" "${binary}" "${knobs}" "${comment}" "${WARMUP}" "${INST_LIMIT}" \
    "$(printf '%s\n' "${apps_for_json[@]}")" <<'PY'
import json, sys
from pathlib import Path

desc_path = Path(sys.argv[1])
binary = sys.argv[2]
knobs = sys.argv[3]
comment = sys.argv[4]
warmup = int(sys.argv[5])
inst_limit = int(sys.argv[6])
workloads = [w for w in sys.argv[7].splitlines() if w]

# Full per-app map (always documented).
PER_APP = {
    "terminal_bench": "T100/W64/I1/D10/stores-off",
    "bfs": "T300/W64/I1/D10/stores-off",
    "dfs": "T300/W64/I1/D10/stores-off",
    "pagerank": "T300/W64/I1/D10/stores-off",
    "core_bench": "T1000/W64/I1/D10/stores-off",
    "appworld": "T10000/W64/I1/D10/stores-off",
    "rocksdb": "T4800/W64/I1/D10/stores-off",
    "duckdb": "T30000/W64/I1/D10/stores-off",
    "clickhouse": "T300/W64/I10/D10/stores-off",
}

desc = json.loads(desc_path.read_text())
desc["architecture"] = "in"
common = (
    f"--icache_size 32768 --inst_limit {inst_limit} "
    f"--full_warmup {warmup} --power_intf_on 1"
)
desc["_comment"] = comment
desc["helios_per_app"] = PER_APP
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
  local app
  PRESENT_APPS=()
  for app in "${HELIOS_APPS[@]}"; do
    if [[ -d "${TRACES_DIR}/${app}" ]] && \
       find "${TRACES_DIR}/${app}" -path '*/traces_simp/*' -name '*.zip' 2>/dev/null | grep -q .; then
      PRESENT_APPS+=("${app}")
    else
      echo "SKIP ${app}: no traces under ${TRACES_DIR}/${app}" >&2
    fi
  done
}

register_traces() {
  discover_present_apps
  if [[ ${#PRESENT_APPS[@]} -eq 0 ]]; then
    echo "ERROR: no Helios apps with traces under ${TRACES_DIR}" >&2
    exit 1
  fi
  echo "Registering ${#PRESENT_APPS[@]} workload(s): ${PRESENT_APPS[*]}"
  python3 -m scripts.register_local_traces \
    --traces-dir "${TRACES_DIR}" \
    --workloads "${PRESENT_APPS[@]}" \
    --warmup "${WARMUP}"
}

ensure_pinned_binary() {
  local do_build="${1:-0}"
  local pinned

  if [[ "${do_build}" == "1" ]]; then
    write_helios_descriptor "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
  fi

  if ! pinned="$(resolve_pinned_binary)"; then
    echo "No cached Scarab binary in ${BUILDS_DIR}; building once..."
    write_helios_descriptor "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
    pinned="$(resolve_pinned_binary)" || {
      echo "ERROR: still no cached binary after build" >&2
      exit 1
    }
  fi

  if [[ ! -s "${BUILDS_DIR}/${pinned}.opt" && ! -s "${BUILDS_DIR}/${pinned}" ]]; then
    echo "ERROR: pinned binary missing: ${pinned}" >&2
    exit 1
  fi
  echo "Using pinned Scarab binary: ${pinned} (no per-app rebuild)"
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
