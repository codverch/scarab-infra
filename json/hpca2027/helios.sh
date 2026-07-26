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
WARMUP=20000000
INST_LIMIT=30000000

HELIOS_APPS=(
  apsp bc bfs community connected_components dfs
  haystack leveldb pagerank sssp_ego_fb triangle_counting
)

declare -A HELIOS_T=(
  [apsp]=300 [bc]=300 [bfs]=300 [community]=300 [connected_components]=300
  [dfs]=300 [haystack]=1000 [leveldb]=4800 [pagerank]=300
  [sssp_ego_fb]=300 [triangle_counting]=300
)
declare -A HELIOS_W=(
  [apsp]=64 [bc]=64 [bfs]=64 [community]=64 [connected_components]=64
  [dfs]=64 [haystack]=64 [leveldb]=64 [pagerank]=64
  [sssp_ego_fb]=64 [triangle_counting]=64
)
declare -A HELIOS_I=(
  [apsp]=1 [bc]=1 [bfs]=1 [community]=1 [connected_components]=1
  [dfs]=1 [haystack]=1 [leveldb]=1 [pagerank]=1
  [sssp_ego_fb]=1 [triangle_counting]=1
)
declare -A HELIOS_D=(
  [apsp]=10 [bc]=10 [bfs]=10 [community]=10 [connected_components]=10
  [dfs]=10 [haystack]=10 [leveldb]=10 [pagerank]=10
  [sssp_ego_fb]=10 [triangle_counting]=10
)
# stores-off for every app
declare -A HELIOS_STORES=(
  [apsp]=0 [bc]=0 [bfs]=0 [community]=0 [connected_components]=0
  [dfs]=0 [haystack]=0 [leveldb]=0 [pagerank]=0
  [sssp_ego_fb]=0 [triangle_counting]=0
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
    comment="Helios per-app: ${app} $(helios_label_for_app "${app}"). 20M warmup + 10M sim. binary=${binary}."
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
