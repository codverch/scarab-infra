#!/usr/bin/env bash
# Ideal fusion: pass 1 discovers fusible load pairs; pass 2 fuses them.
#
# Pass 1 writes candidates to:
#   /dev/shm/baseline/ideal_fusion_candidates/{workload}/{cluster_id}.csv
# Pass 2 reads the same paths (descriptor: ideal-fusion.json, experiment/config
# "ideal-fusion").
# Outputs land under simulations/ideal-fusion/{workload}/{cluster_id}/ (not "pass2").
#
# Usage:
#   ./ideal-fusion.sh              # register + pass1, then pass2 when pass1 completes
#   ./ideal-fusion.sh --pass1-only # pass 1 only
#   ./ideal-fusion.sh --pass2-only # pass 2 only (requires pass1 candidates)
#   ./ideal-fusion.sh --build      # rebuild/stage Scarab once, then full workflow
#   ./ideal-fusion.sh --dry-run
#   ./ideal-fusion.sh --status
#   ./ideal-fusion.sh --verify-pass2  # check candidate paths + mcpat outputs for running/completed pass2

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PASS1_DESCRIPTOR="hpca2027/ideal-fusion-pass1"
PASS2_DESCRIPTOR="hpca2027/ideal-fusion"
PASS1_JSON="${INFRA_DIR}/json/hpca2027/ideal-fusion-pass1.json"
PASS2_JSON="${INFRA_DIR}/json/hpca2027/ideal-fusion.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
CANDIDATES_DIR="/dev/shm/baseline/ideal_fusion_candidates"
SIM_ROOT="/users/deepmish/scarab/src/simulations"
PASS1_SCENARIO="pass1"
PASS2_SCENARIO="ideal-fusion"
SCARAB_PATH="/users/deepmish/scarab"
BUILDS_DIR="${INFRA_DIR}/scarab_builds"
WARMUP=20000000
INST_LIMIT=30000000
MCPAT_BIN="${MCPAT_BIN:-/users/deepmish/toolchain/bin/mcpat}"
CACTI_BIN="${CACTI_BIN:-/users/deepmish/toolchain/bin/cacti}"

IF_APPS=(
  appworld bfs-init bfs-web-google clickhouse corebench dfs-init dfs-web-google
  duckdb grpc leveldb memcached pagerank-gnutella31 pagerank-init rocksdb
  sqlite sssp-ego-facebook sssp-init terminal_bench
)

# Workload / candidate / result dirs match simpoint_traces names 1:1.
trace_dir_for_app() {
  echo "$1"
}

resolve_pinned_binary() {
  local link target base f
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
  for f in "${BUILDS_DIR}"/scarab_*_0.opt; do
    [[ -f "$f" && -s "$f" ]] || continue
    base="$(basename "$f" .opt)"
    printf '%s' "${base}"
    return 0
  done
  for f in "${BUILDS_DIR}"/scarab_*.opt; do
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
    echo "ERROR: no allbench_traces image; run: ./sci --build-image allbench_traces" >&2
    return 1
  fi
  echo "Retagging allbench_traces:${existing} -> ${img}"
  docker tag "allbench_traces:${existing}" "${img}"
}

write_descriptor_binary() {
  local json_path="$1"
  local binary="$2"
  python3 - "${json_path}" "${binary}" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
binary = sys.argv[2]
desc = json.loads(path.read_text())
for cfg in desc.get("configurations", {}).values():
    if isinstance(cfg, dict) and "binary" in cfg:
        cfg["binary"] = binary
path.write_text(json.dumps(desc, indent=2) + "\n")
print(f"Updated {path} binary={binary}")
PY
}

discover_present_apps() {
  local app trace_dir
  PRESENT_APPS=()
  for app in "${IF_APPS[@]}"; do
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
    echo "ERROR: no ideal-fusion apps with traces under ${TRACES_DIR}" >&2
    exit 1
  fi
  local app trace_dirs=()
  for app in "${PRESENT_APPS[@]}"; do
    trace_dirs+=("$(trace_dir_for_app "${app}")")
  done
  echo "Registering ${#PRESENT_APPS[@]} workload(s) with HPCA2027 aliases: ${PRESENT_APPS[*]}"
  python3 -m scripts.register_local_traces \
    --traces-dir "${TRACES_DIR}" \
    --workloads "${trace_dirs[@]}" \
    --warmup "${WARMUP}"
}

ensure_candidates_dir() {
  mkdir -p "${CANDIDATES_DIR}"
  echo "Candidates dir: ${CANDIDATES_DIR}"
}

ensure_docker_mcpat_cacti() {
  local build_dir="/dev/shm/baseline/mcpat_build"
  local mcpat_src="/users/deepmish/toolchain/source"
  mkdir -p "${build_dir}"
  if [[ ! -x "${build_dir}/mcpat" ]] || ! "${build_dir}/mcpat" 2>&1 | head -1 | grep -q McPAT; then
    echo "Building docker-compatible mcpat/cacti (glibc 2.31)..."
    ensure_docker_image_tag
    docker run --rm \
      -v "${mcpat_src}:/mcpat-src:ro" \
      -v "${build_dir}:/out" \
      allbench_traces:00022fc bash -c '
set -e
cp -r /mcpat-src /tmp/mcpat
cd /tmp/mcpat
make clean 2>/dev/null || true
rm -rf obj_opt mcpat
make -j$(nproc)
cp mcpat /out/mcpat
cd cacti
make clean 2>/dev/null || true
rm -rf obj_opt
make CXX=g++ CC=gcc -j$(nproc)
cp cacti /out/cacti
'
  fi
  MCPAT_BIN="${build_dir}/mcpat"
  CACTI_BIN="${build_dir}/cacti"
  export MCPAT_BIN CACTI_BIN
}

ensure_pinned_binary() {
  local do_build="${1:-0}"
  local pinned

  ensure_docker_mcpat_cacti

  if [[ "${do_build}" == "1" ]]; then
    write_descriptor_binary "${PASS1_JSON}" "scarab_current"
    write_descriptor_binary "${PASS2_JSON}" "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${PASS1_DESCRIPTOR}"
    ./sci --build-scarab "${PASS2_DESCRIPTOR}"
  fi

  if ! pinned="$(resolve_pinned_binary)"; then
    echo "No cached Scarab binary in ${BUILDS_DIR}; building once..."
    write_descriptor_binary "${PASS1_JSON}" "scarab_current"
    write_descriptor_binary "${PASS2_JSON}" "scarab_current"
    ensure_docker_image_tag
    ./sci --build-scarab "${PASS1_DESCRIPTOR}"
    ./sci --build-scarab "${PASS2_DESCRIPTOR}"
    pinned="$(resolve_pinned_binary)" || {
      echo "ERROR: still no cached binary after build" >&2
      exit 1
    }
  fi

  write_descriptor_binary "${PASS1_JSON}" "${pinned}"
  write_descriptor_binary "${PASS2_JSON}" "${pinned}"

  # Stage pass1/pass2 bindirs (power_intf + mcpat) without rebuilding scarab_current.
  ensure_docker_image_tag
  ./sci --build-scarab "${PASS1_DESCRIPTOR}"
  ./sci --build-scarab "${PASS2_DESCRIPTOR}"

  # Deploy docker-compatible mcpat/cacti into staged bindirs
  # (scarab_stage dirs are named after each descriptor's "experiment" field:
  #  pass1 -> ideal-fusion-pass1, pass2 -> ideal-fusion)
  for exp in ideal-fusion-pass1 ideal-fusion; do
    stage="${SCARAB_PATH}/src/scarab_stage/${exp}/scarab/bin"
    cp "${MCPAT_BIN}" "${stage}/mcpat"
    cp "${CACTI_BIN}" "${stage}/cacti"
    chmod +x "${stage}/mcpat" "${stage}/cacti"
  done

  # Re-copy scarab_globals after scarab_paths fix
  for exp in ideal-fusion-pass1 ideal-fusion; do
    stage="${SCARAB_PATH}/src/scarab_stage/${exp}/scarab/bin/scarab_globals"
    mkdir -p "${stage}"
    cp "${SCARAB_PATH}/bin/scarab_globals/"* "${stage}/"
  done

  # Verify staged power tools
  local stage1="${SCARAB_PATH}/src/scarab_stage/ideal-fusion-pass1/scarab/bin"
  local stage2="${SCARAB_PATH}/src/scarab_stage/ideal-fusion/scarab/bin"
  for stage in "${stage1}" "${stage2}"; do
    [[ -f "${stage}/mcpat" ]] || { echo "ERROR: missing ${stage}/mcpat" >&2; exit 1; }
    [[ -f "${stage}/power/power_intf.py" ]] || { echo "ERROR: missing ${stage}/power/power_intf.py" >&2; exit 1; }
    [[ -f "${stage}/scarab_globals/scarab" ]] || [[ -f "${SCARAB_PATH}/src/scarab_stage/ideal-fusion-pass1/scarab/src/${pinned}.opt" ]] || true
  done
  echo "Using pinned Scarab binary: ${pinned}"
  PINNED_BINARY="${pinned}"
}

get_simpoint_cluster_ids() {
  local app="$1"
  python3 - "${app}" <<'PY'
import json, sys
from pathlib import Path

app = sys.argv[1]
db = json.loads(Path("workloads/workloads_db.json").read_text())
entry = db.get("datacenter", {}).get("datacenter", {}).get(app)
if not entry:
    sys.exit(1)
for sp in entry.get("simpoints", []):
    print(sp["cluster_id"])
PY
}

simpoint_status() {
  local pass="$1" app="$2" cluster="$3"
  local scenario="${PASS1_SCENARIO}"
  if [[ "${pass}" == "2" ]]; then
    scenario="${PASS2_SCENARIO}"
  fi
  local out="${SIM_ROOT}/${scenario}/${app}/${cluster}"
  if [[ ! -f "${out}/sim.log" ]]; then
    return 1
  fi
  # power_intf.c asserts (:139 PARAMS.out-flush race, :151 reopen race) are
  # benign: this Scarab build invokes McPAT/CACTI around PARAMS.out flush /
  # power_model_results reopen. They fire only after "** Core N Finished",
  # i.e. after all performance stats are written, so they do not invalidate
  # the simpoint. backfill_power() re-runs power_intf.pl afterward to ensure
  # power_model_results exists. Any OTHER assert means a real failure.
  if grep -q 'ASSERT FAILED' "${out}/sim.log" 2>/dev/null && \
     grep 'ASSERT FAILED' "${out}/sim.log" 2>/dev/null | grep -qv 'power_intf\.c:'; then
    return 2
  fi
  if [[ -f "${out}/bp.stat.0.csv" ]]; then
    return 0
  fi
  return 1
}

backfill_power() {
  local pass="$1"
  local scenario="${PASS1_SCENARIO}"
  local stage="${SCARAB_PATH}/src/scarab_stage/ideal-fusion-pass1/scarab/bin"
  if [[ "${pass}" == "2" ]]; then
    scenario="${PASS2_SCENARIO}"
    stage="${SCARAB_PATH}/src/scarab_stage/ideal-fusion/scarab/bin"
  fi
  local power_intf_pl="${stage}/power/power_intf.pl"
  if [[ ! -f "${power_intf_pl}" ]]; then
    echo "backfill_power: no ${power_intf_pl}, skipping (pass ${pass} not staged?)" >&2
    return 0
  fi
  ensure_docker_mcpat_cacti
  local app cluster fixed=0 failed=0
  for app in "${PRESENT_APPS[@]}"; do
    while IFS= read -r cluster; do
      [[ -n "${cluster}" ]] || continue
      local out="${SIM_ROOT}/${scenario}/${app}/${cluster}"
      [[ -f "${out}/power_model_results" ]] && continue
      if [[ ! -f "${out}/mcpat_infile.xml" || ! -f "${out}/cacti_infile.cfg" || ! -f "${out}/PARAMS.out" ]]; then
        continue
      fi
      if (cd "${out}" && perl "${power_intf_pl}" "${MCPAT_BIN}" "${CACTI_BIN}" . 0 0 \
            > backfill_power.log 2>&1); then
        if [[ -f "${out}/power_model_results" ]]; then
          fixed=$((fixed + 1))
        else
          echo "backfill_power: ${app}/${cluster} ran but produced no power_model_results" >&2
          failed=$((failed + 1))
        fi
      else
        echo "backfill_power: FAILED ${app}/${cluster} (see ${out}/backfill_power.log)" >&2
        failed=$((failed + 1))
      fi
    done < <(get_simpoint_cluster_ids "${app}" 2>/dev/null || true)
  done
  echo "backfill_power pass ${pass}: fixed=${fixed} failed=${failed}"
  [[ "${failed}" -eq 0 ]]
}

count_pass_status() {
  local pass="$1"
  local app cluster total=0 ok=0 fail=0 pending=0 st
  for app in "${PRESENT_APPS[@]}"; do
    while IFS= read -r cluster; do
      [[ -n "${cluster}" ]] || continue
      total=$((total + 1))
      simpoint_status "${pass}" "${app}" "${cluster}"
      st=$?
      if [[ "${st}" -eq 0 ]]; then
        ok=$((ok + 1))
      elif [[ "${st}" -eq 2 ]]; then
        fail=$((fail + 1))
      else
        pending=$((pending + 1))
      fi
    done < <(get_simpoint_cluster_ids "${app}" 2>/dev/null || true)
  done
  echo "pass${pass}: total=${total} ok=${ok} fail=${fail} pending=${pending}"
}

wait_for_pass() {
  local pass="$1"
  local interval="${2:-120}"
  echo "Waiting for ideal-fusion pass ${pass} simpoints to finish (poll every ${interval}s)..."
  while true; do
    count_pass_status "${pass}"
    local pending
    pending="$(count_pass_status "${pass}" | sed -n 's/.*pending=\([0-9]*\).*/\1/p')"
    local fail
    fail="$(count_pass_status "${pass}" | sed -n 's/.*fail=\([0-9]*\).*/\1/p')"
    if [[ "${pending}" -eq 0 ]]; then
      if [[ "${fail}" -gt 0 ]]; then
        echo "ERROR: pass ${pass} finished with ${fail} failed simpoint(s)" >&2
        return 1
      fi
      echo "Pass ${pass} complete."
      return 0
    fi
    sleep "${interval}"
  done
}

verify_pass1_candidates() {
  local app cluster missing=0
  echo "Verifying pass1 candidate CSVs under ${CANDIDATES_DIR}..."
  for app in "${PRESENT_APPS[@]}"; do
    while IFS= read -r cluster; do
      [[ -n "${cluster}" ]] || continue
      local csv="${CANDIDATES_DIR}/${app}/${cluster}.csv"
      if [[ ! -f "${csv}" ]]; then
        echo "MISSING candidate: ${csv}" >&2
        missing=$((missing + 1))
      elif [[ ! -s "${csv}" ]]; then
        echo "EMPTY candidate (ok if no pairs): ${csv}"
      else
        head -1 "${csv}" | grep -q load1_pc || echo "WARN: unexpected header in ${csv}" >&2
      fi
    done < <(get_simpoint_cluster_ids "${app}" 2>/dev/null || true)
  done
  if [[ "${missing}" -gt 0 ]]; then
    echo "ERROR: ${missing} pass1 candidate file(s) missing" >&2
    return 1
  fi
  echo "All pass1 candidate paths present."
}

verify_pass2_reads_and_power() {
  local app cluster bad_read=0 bad_power=0 checked=0
  echo "Verifying pass2 candidate reads and power outputs..."
  for app in "${PRESENT_APPS[@]}"; do
    while IFS= read -r cluster; do
      [[ -n "${cluster}" ]] || continue
      local out="${SIM_ROOT}/${PASS2_SCENARIO}/${app}/${cluster}"
      local csv="${CANDIDATES_DIR}/${app}/${cluster}.csv"
      local simlog="${out}/sim.log"
      if [[ ! -f "${simlog}" ]]; then
        echo "SKIP (no sim.log): ${app}/${cluster}" >&2
        continue
      fi
      checked=$((checked + 1))
      if ! grep -q "Ideal fusion pass 2: loaded" "${simlog}" 2>/dev/null; then
        echo "BAD pass2 read: ${app}/${cluster} — no 'loaded candidate pair(s)' in sim.log" >&2
        bad_read=$((bad_read + 1))
      fi
      if [[ -f "${csv}" ]]; then
        local path_in_log
        path_in_log="$(grep -o '/dev/shm/baseline/ideal_fusion_candidates[^ ]*' "${simlog}" | head -1 || true)"
        if [[ -n "${path_in_log}" && "${path_in_log}" != "${csv}" ]]; then
          echo "WARN: sim.log path ${path_in_log} != expected ${csv}" >&2
        fi
      fi
      if ls "${out}"/*mcpat_infile.xml >/dev/null 2>&1 && \
         [[ -f "${out}/power_model_results" ]]; then
        : # power outputs present
      elif grep -q "power_intf_on 1" "${out}/PARAMS.in" 2>/dev/null || \
           grep -q "power_intf_on 1" "${PASS2_JSON}" 2>/dev/null; then
        if ! ls "${out}"/*mcpat_infile.xml >/dev/null 2>&1; then
          echo "MISSING mcpat_infile.xml: ${app}/${cluster}" >&2
          bad_power=$((bad_power + 1))
        fi
        if [[ ! -f "${out}/power_model_results" ]]; then
          echo "MISSING power_model_results: ${app}/${cluster}" >&2
          bad_power=$((bad_power + 1))
        fi
      fi
    done < <(get_simpoint_cluster_ids "${app}" 2>/dev/null || true)
  done
  echo "Checked ${checked} pass2 simpoint(s): bad_read=${bad_read} bad_power=${bad_power}"
  [[ "${bad_read}" -eq 0 && "${bad_power}" -eq 0 ]]
}

run_pass1() {
  echo "=== Starting ideal-fusion pass 1 ==="
  ./sci --sim "${PASS1_DESCRIPTOR}"
}

run_pass2() {
  verify_pass1_candidates
  echo "=== Starting ideal-fusion pass 2 ==="
  ./sci --sim "${PASS2_DESCRIPTOR}"
}

run_full() {
  register_traces
  ensure_candidates_dir
  ensure_pinned_binary 0
  run_pass1
  wait_for_pass 1
  backfill_power 1
  verify_pass1_candidates
  run_pass2
  wait_for_pass 2
  backfill_power 2
  verify_pass2_reads_and_power
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [--build|--pass1-only|--pass2-only|--verify-pass2|--dry-run|--status|--help]

Ideal fusion workflow:
  pass1 -> ${CANDIDATES_DIR}/{workload}/{cluster_id}.csv
  pass2 reads same paths; power via --power_intf_on 1 + staged mcpat
EOF
}

main() {
  cd "${INFRA_DIR}"
  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi

  case "${1:-}" in
    ""|--all)
      run_full
      ;;
    --build)
      register_traces
      ensure_candidates_dir
      ensure_pinned_binary 1
      run_pass1
      wait_for_pass 1
      verify_pass1_candidates
      run_pass2
      wait_for_pass 2
      verify_pass2_reads_and_power
      ;;
    --pass1-only)
      register_traces
      ensure_candidates_dir
      ensure_pinned_binary 0
      run_pass1
      ;;
    --pass2-only)
      register_traces
      ensure_pinned_binary 0
      run_pass2
      ;;
    --verify-pass2)
      discover_present_apps
      verify_pass2_reads_and_power
      ;;
    --dry-run)
      discover_present_apps
      echo "Present apps: ${PRESENT_APPS[*]:-none}"
      echo "Candidates: ${CANDIDATES_DIR}"
      if pinned="$(resolve_pinned_binary 2>/dev/null)"; then
        echo "Pinned binary: ${pinned}"
      else
        echo "No pinned binary yet"
      fi
      local trace_dirs=()
      for app in "${PRESENT_APPS[@]:-}"; do
        trace_dirs+=("$(trace_dir_for_app "${app}")")
      done
      if [[ ${#trace_dirs[@]} -gt 0 ]]; then
        python3 -m scripts.register_local_traces \
          --traces-dir "${TRACES_DIR}" --warmup "${WARMUP}" --dry-run \
          --workloads "${trace_dirs[@]}"
      fi
      ;;
    --status)
      discover_present_apps
      count_pass_status 1 || true
      count_pass_status 2 || true
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
