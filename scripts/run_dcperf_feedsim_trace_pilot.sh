#!/usr/bin/env bash

set -euo pipefail

readonly ROOT_DIR="${DCPERF_ROOT:-/proj/datacntr-effcy-PG0/${USER}/hpca2027_dcperf}"
readonly DCPERF_DIR="${ROOT_DIR}/src/DCPerf"
readonly SCARAB_DIR="${ROOT_DIR}/src/scarab-hpca2027-characterization"
readonly BUILD_DIR="${SCARAB_DIR}/src/build/opt"
readonly DR_DIR="${BUILD_DIR}/deps/dynamorio"
readonly FEEDSIM_DIR="${DCPERF_DIR}/benchmarks/feedsim/src"
readonly OUT_DIR="${1:-${ROOT_DIR}/traces/feedsim_pilot_100m}"
readonly PORT="${FEEDSIM_PORT:-31212}"
readonly TRACE_AFTER_INSTRUCTIONS="${TRACE_AFTER_INSTRUCTIONS:-350000000000}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-100000000}"
readonly QPS="${FEEDSIM_QPS:-0.02}"
readonly DRIVER_SECONDS="${FEEDSIM_DRIVER_SECONDS:-300}"
readonly DRAIN_SECONDS="${FEEDSIM_DRAIN_SECONDS:-30}"

client_pid=""
trace_pid=""

stop_pid() {
  local pid="$1"

  if ! kill -0 "${pid}" 2>/dev/null; then
    return
  fi

  kill -INT "${pid}" 2>/dev/null || true
  for _ in $(seq 1 60); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "${pid}" 2>/dev/null; then
    kill -TERM "${pid}" 2>/dev/null || true
    for _ in $(seq 1 5); do
      if ! kill -0 "${pid}" 2>/dev/null; then
        break
      fi
      sleep 1
    done
  fi
  if kill -0 "${pid}" 2>/dev/null; then
    kill -KILL "${pid}" 2>/dev/null || true
  fi
  wait "${pid}" 2>/dev/null || true
}

stop_process_tree() {
  local pid="$1"
  local child

  while read -r child; do
    [[ -n "${child}" ]] && stop_process_tree "${child}"
  done < <(pgrep -P "${pid}" 2>/dev/null || true)
  stop_pid "${pid}"
}

cleanup() {
  if [[ -n "${client_pid}" ]] && kill -0 "${client_pid}" 2>/dev/null; then
    stop_process_tree "${client_pid}"
  fi
  if [[ -n "${trace_pid}" ]] && kill -0 "${trace_pid}" 2>/dev/null; then
    stop_pid "${trace_pid}"
  fi
}
trap cleanup EXIT INT TERM

for path in \
  "${DR_DIR}/bin64/drrun" \
  "${DR_DIR}/clients/bin64/drraw2trace" \
  "${FEEDSIM_DIR}/build/workloads/ranking/LeafNodeRank" \
  "${FEEDSIM_DIR}/build/workloads/ranking/DriverNodeRank"; do
  if [[ ! -x "${path}" ]]; then
    echo "Missing executable: ${path}" >&2
    exit 1
  fi
done

if pgrep -x LeafNodeRank >/dev/null; then
  echo "A FeedSim LeafNodeRank process is already running" >&2
  exit 1
fi

if [[ -e "${OUT_DIR}" ]]; then
  echo "Refusing to overwrite existing output: ${OUT_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}/raw_trace" "${OUT_DIR}/logs"

# FeedSim's helper hard-codes a shared /tmp log. Use a private patched copy so
# concurrent or prior root-owned runs cannot make the client fail.
search_qps_script="${OUT_DIR}/search_qps.sh"
cp "${FEEDSIM_DIR}/scripts/search_qps.sh" "${search_qps_script}"
sed -i \
  's|^BREPS_LFILE=.*$|BREPS_LFILE="${FEEDSIM_BREPS_LFILE:-/tmp/feedsim_log.txt}"|' \
  "${search_qps_script}"
chmod 755 "${search_qps_script}"

monitor_port=$((PORT - 1000))
client_monitor_port=$((PORT - 2000))
nproc_count="$(nproc)"
ranking_threads=$((nproc_count * 7 / 20))
srv_io_threads=$((nproc_count * 7 / 20))
if (( ranking_threads < 1 )); then ranking_threads=1; fi
if (( srv_io_threads < 1 )); then srv_io_threads=1; fi
if (( srv_io_threads > 55 )); then srv_io_threads=55; fi
driver_threads=$((nproc_count / 5))
if (( driver_threads < 4 )); then driver_threads=4; fi

(
  for _ in $(seq 1 180); do
    if nc -z 127.0.0.1 "${PORT}"; then
      FEEDSIM_BREPS_LFILE="${OUT_DIR}/feedsim_state.log" \
        exec "${search_qps_script}" \
        -s 95p \
        -t "${DRIVER_SECONDS}" \
        -w "${DRAIN_SECONDS}" \
        -m 0 \
        -q "${QPS}" \
        -o "${OUT_DIR}/feedsim_driver.csv" \
        -- "${FEEDSIM_DIR}/build/workloads/ranking/DriverNodeRank" \
          --server "127.0.0.1:${PORT}" \
          --monitor_port "${client_monitor_port}" \
          --threads "${driver_threads}" \
          --connections 4
    fi
    sleep 1
  done
  echo "FeedSim server did not open port ${PORT} within 180 seconds" >&2
  exit 1
) >"${OUT_DIR}/logs/client.log" 2>&1 &
client_pid=$!

cd "${FEEDSIM_DIR}"
MALLOC_CONF=narenas:20,dirty_decay_ms:5000 \
  "${DR_DIR}/bin64/drrun" \
    -t drcachesim \
    -jobs 20 \
    -outdir "${OUT_DIR}/raw_trace" \
    -offline \
    -no_split_windows \
    -trace_after_instrs "${TRACE_AFTER_INSTRUCTIONS}" \
    -trace_for_instrs "${TRACE_INSTRUCTIONS}" \
    -- "${FEEDSIM_DIR}/build/workloads/ranking/LeafNodeRank" \
      --port "${PORT}" \
      --monitor_port "${monitor_port}" \
      --graph_scale 21 \
      --graph_subset 2000000 \
      --threads "${nproc_count}" \
      --cpu_threads "${ranking_threads}" \
      --timekeeper_threads 2 \
      --io_threads 4 \
      --srv_threads 8 \
      --srv_io_threads "${srv_io_threads}" \
      --num_objects 2000 \
      --graph_max_iters 1 \
      --noaffinity \
      --min_icache_iterations 1600000 \
      >"${OUT_DIR}/logs/drrun.log" 2>&1 &
trace_pid=$!

if ! wait "${client_pid}"; then
  echo "FeedSim client failed; see ${OUT_DIR}/logs/client.log" >&2
  exit 1
fi
client_pid=""

if kill -0 "${trace_pid}" 2>/dev/null; then
  stop_pid "${trace_pid}"
fi
trace_pid=""

mapfile -t dr_dirs < <(find "${OUT_DIR}/raw_trace" -maxdepth 1 -type d -name 'drmemtrace.*.dir')
if [[ "${#dr_dirs[@]}" -ne 1 ]]; then
  echo "Expected one drmemtrace directory, found ${#dr_dirs[@]}" >&2
  exit 1
fi

readonly dr_dir="${dr_dirs[0]}"
readonly raw_dir="${dr_dir}/raw"
test -s "${raw_dir}/modules.log"
raw_trace_file="$(find "${raw_dir}" -type f -name '*.raw.*' -size +0c -print -quit)"
if [[ -z "${raw_trace_file}" ]]; then
  echo "No non-empty raw trace file was generated" >&2
  exit 1
fi

"${DR_DIR}/clients/bin64/drraw2trace" \
  -jobs 20 \
  -indir "${raw_dir}" \
  -chunk_instr_count 10000000 \
  >"${OUT_DIR}/logs/raw2trace.log" 2>&1

readonly trace_dir="${dr_dir}/trace"
mapfile -t trace_zips < <(find "${trace_dir}" -type f -name '*.trace.zip' | sort)
if [[ "${#trace_zips[@]}" -lt 1 ]]; then
  echo "No converted thread trace zips found" >&2
  exit 1
fi

chunk_count=0
: >"${OUT_DIR}/logs/unzip-test.log"
for trace_zip in "${trace_zips[@]}"; do
  unzip -t "${trace_zip}" >>"${OUT_DIR}/logs/unzip-test.log"
  zip_chunks="$(unzip -Z1 "${trace_zip}" | grep -c '^chunk\.' || true)"
  chunk_count=$((chunk_count + zip_chunks))
done

"${DR_DIR}/bin64/drrun" \
  -t drcachesim \
  -indir "${trace_dir}" \
  -tool basic_counts \
  >"${OUT_DIR}/logs/basic_counts.log" 2>&1
fetched_instructions="$(awk '/total \(fetched\) instructions/ {print $1; exit}' \
  "${OUT_DIR}/logs/basic_counts.log")"
if [[ ! "${fetched_instructions}" =~ ^[0-9]+$ ]] || \
    (( fetched_instructions < TRACE_INSTRUCTIONS )); then
  echo "Converted trace has ${fetched_instructions:-unknown} fetched instructions; expected at least ${TRACE_INSTRUCTIONS}" >&2
  exit 1
fi

printf '%s\n' \
  "status=trace_valid" \
  "trace_dir=${trace_dir}" \
  "trace_zip_count=${#trace_zips[@]}" \
  "modules_dir=${raw_dir}" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "fetched_instructions=${fetched_instructions}" \
  "trace_counter=trace_for_instrs" \
  "chunk_count=${chunk_count}" \
  "server_port=${PORT}" \
  "requested_qps=${QPS}" \
  "drain_seconds=${DRAIN_SECONDS}" \
  >"${OUT_DIR}/pilot-summary.txt"

cat "${OUT_DIR}/pilot-summary.txt"
