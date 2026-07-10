#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  exec sudo --preserve-env=DCPERF_ROOT,LOCAL_TRACE_ROOT,TRACE_AFTER_INSTRUCTIONS,TRACE_INSTRUCTIONS,TAO_WARMUP_SECONDS,TAO_TEST_SECONDS,TAO_CLIENTS_PER_THREAD \
    "$0" "$@"
fi

readonly BENCH_USER="${SUDO_USER:-${USER}}"
readonly ROOT_DIR="${DCPERF_ROOT:-/proj/datacntr-effcy-PG0/${BENCH_USER}/hpca2027_dcperf}"
readonly DCPERF_DIR="${ROOT_DIR}/src/DCPerf"
readonly SCARAB_DIR="${ROOT_DIR}/src/scarab-hpca2027-characterization"
readonly BUILD_DIR="${SCARAB_DIR}/src/build/opt"
readonly DR_DIR="${BUILD_DIR}/deps/dynamorio"
readonly DRMEMTRACE_CLIENT="${DR_DIR}/clients/lib64/release/libdrmemtrace.so"
readonly TAO_DIR="${DCPERF_DIR}/benchmarks/tao_bench"
readonly TAO_PACKAGE_DIR="${DCPERF_DIR}/packages/tao_bench"
readonly PRIVATE_LIB_DIR="${TAO_DIR}/build-deps/lib"
readonly SERVER_BIN="${TAO_DIR}/tao_bench_server"
readonly OUT_DIR="${1:-${ROOT_DIR}/traces/tao_server_pilot_1m}"
readonly LOCAL_TRACE_ROOT="${LOCAL_TRACE_ROOT:-/tmp}"
readonly STAGING_DIR="${LOCAL_TRACE_ROOT}/hpca2027_dcperf_${BENCH_USER}_tao_$(basename "${OUT_DIR}")"
readonly RAW_TRACE_DIR="${STAGING_DIR}/raw_trace"
readonly TRACE_AFTER_INSTRUCTIONS="${TRACE_AFTER_INSTRUCTIONS:-65000000000}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-1000000}"
readonly WARMUP_SECONDS="${TAO_WARMUP_SECONDS:-10}"
readonly TEST_SECONDS="${TAO_TEST_SECONDS:-30}"
readonly CLIENTS_PER_THREAD="${TAO_CLIENTS_PER_THREAD:-16}"
readonly PORT=11211
readonly NPROC="$(nproc)"
readonly FAST_THREADS="$((NPROC * 75 / 100))"
readonly DISPATCHERS="$((FAST_THREADS * 25 / 100))"
readonly SLOW_THREADS="$((FAST_THREADS * 3))"
readonly MEMSIZE_MB=3072

server_pid=""
drrun_pid=""
trace_monitor_pid=""
trace_activation_monitor_pid=""
client_phase_monitor_pid=""
permission_monitor_pid=""
restart_system_memcached=0

stop_pid() {
  local pid="$1"

  if ! kill -0 "${pid}" 2>/dev/null; then
    return
  fi
  kill -TERM "${pid}" 2>/dev/null || true
  for _ in $(seq 1 30); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return
    fi
    sleep 1
  done
  kill -KILL "${pid}" 2>/dev/null || true
}

cleanup() {
  [[ -n "${trace_monitor_pid}" ]] && stop_pid "${trace_monitor_pid}"
  [[ -n "${trace_activation_monitor_pid}" ]] && stop_pid "${trace_activation_monitor_pid}"
  [[ -n "${client_phase_monitor_pid}" ]] && stop_pid "${client_phase_monitor_pid}"
  [[ -n "${permission_monitor_pid}" ]] && stop_pid "${permission_monitor_pid}"
  [[ -n "${server_pid}" ]] && stop_pid "${server_pid}"
  [[ -n "${drrun_pid}" ]] && stop_pid "${drrun_pid}"
  if (( restart_system_memcached == 1 )); then
    systemctl start memcached 2>/dev/null || true
  fi
  if [[ -d "${OUT_DIR}" ]]; then
    chown -R "${BENCH_USER}:$(id -gn "${BENCH_USER}")" "${OUT_DIR}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for path in \
  "${DR_DIR}/bin64/drrun" \
  "${DR_DIR}/clients/bin64/drraw2trace" \
  "${DRMEMTRACE_CLIENT}" \
  "${SERVER_BIN}" \
  "${TAO_PACKAGE_DIR}/run.py" \
  "${PRIVATE_LIB_DIR}/libssl.so.3" \
  "${PRIVATE_LIB_DIR}/libcrypto.so.3"; do
  if [[ ! -x "${path}" && ! -r "${path}" ]]; then
    echo "Missing TaoBench dependency: ${path}" >&2
    exit 1
  fi
done

if pgrep -x tao_bench_server >/dev/null || pgrep -x tao_bench_client >/dev/null; then
  echo "A TaoBench process is already running" >&2
  exit 1
fi
if [[ -e "${OUT_DIR}" ]]; then
  echo "Refusing to overwrite existing output: ${OUT_DIR}" >&2
  exit 1
fi
if [[ -e "${STAGING_DIR}" ]]; then
  echo "Refusing to overwrite existing local staging output: ${STAGING_DIR}" >&2
  exit 1
fi

umask 000
mkdir -p "${RAW_TRACE_DIR}" "${OUT_DIR}/logs"
chmod 0777 "${STAGING_DIR}" "${RAW_TRACE_DIR}"
printf 'pilot_start=%s\n' "$(date --iso-8601=ns)" >"${OUT_DIR}/alignment-events.txt"
printf 'local_trace_staging=%s\n' "${STAGING_DIR}" >>"${OUT_DIR}/alignment-events.txt"

if systemctl is-active --quiet memcached; then
  restart_system_memcached=1
  systemctl stop memcached
fi
if nc -z 127.0.0.1 "${PORT}"; then
  echo "Port ${PORT} is still in use after stopping system memcached" >&2
  exit 1
fi

export LD_LIBRARY_PATH="${PRIVATE_LIB_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
ldd "${SERVER_BIN}" >"${OUT_DIR}/logs/server-ldd.log"
if ! grep -Fq "libssl.so.3 => ${PRIVATE_LIB_DIR}/libssl.so.3" "${OUT_DIR}/logs/server-ldd.log" || \
    ! grep -Fq "libcrypto.so.3 => ${PRIVATE_LIB_DIR}/libcrypto.so.3" "${OUT_DIR}/logs/server-ldd.log"; then
  echo "TaoBench did not resolve both TLS libraries from ${PRIVATE_LIB_DIR}" >&2
  exit 1
fi

cd "${TAO_DIR}"
(
  while true; do
    find "${RAW_TRACE_DIR}" -type d -exec chmod 0777 {} + 2>/dev/null || true
    sleep 0.05
  done
) &
permission_monitor_pid=$!

"${DR_DIR}/bin64/drrun" \
  -pidfile "${OUT_DIR}/server.pid" \
  -ops "-no_follow_children" \
  -t drcachesim \
  -jobs 20 \
  -outdir "${RAW_TRACE_DIR}" \
  -offline \
  -no_split_windows \
  -trace_after_instrs "${TRACE_AFTER_INSTRUCTIONS}" \
  -trace_for_instrs "${TRACE_INSTRUCTIONS}" \
  -- "${SERVER_BIN}" \
  -c 180000 \
  -u nobody \
  -m "${MEMSIZE_MB}" \
  -t "${FAST_THREADS}" \
  -B binary \
  -p "${PORT}" \
  -I 16m \
  -Z \
  -o "lru_crawler,tao_it_gen_file=${TAO_DIR}/leader_sizes.json,tao_max_item_size=65536,tao_gen_payload=0,tao_slow_dispatchers=${DISPATCHERS},tao_num_slow_threads=${SLOW_THREADS},tao_max_slow_reqs=1024,tao_worker_sleep_ns=100,tao_dispatcher_sleep_ns=100,tao_slow_sleep_ns=100,tao_slow_path_sleep_us=0,tao_compress_items=1,tao_stats_sleep_ms=5000,tao_slow_use_semaphore=0,tao_pin_threads=0,tao_smart_nanosleep=0,ssl_chain_cert=${TAO_DIR}/certs/example.crt,ssl_key=${TAO_DIR}/certs/example.key" \
  >"${OUT_DIR}/logs/server.log" 2>&1 &
drrun_pid=$!

(
  while kill -0 "${drrun_pid}" 2>/dev/null; do
    if grep -q 'Hit delay threshold: enabling tracing' "${OUT_DIR}/logs/server.log" 2>/dev/null; then
      printf 'trace_window_enabled=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
      exit 0
    fi
    sleep 0.05
  done
) &
trace_activation_monitor_pid=$!

(
  while kill -0 "${drrun_pid}" 2>/dev/null; do
    if find "${RAW_TRACE_DIR}" -type f -name '*.raw.*' -size +128c -print -quit | grep -q .; then
      printf 'first_substantive_raw_write=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
      exit 0
    fi
    sleep 0.1
  done
) &
trace_monitor_pid=$!

for _ in $(seq 1 100); do
  [[ -s "${OUT_DIR}/server.pid" ]] && break
  kill -0 "${drrun_pid}" 2>/dev/null || break
  sleep 0.1
done
if [[ ! -s "${OUT_DIR}/server.pid" ]]; then
  echo "DynamoRIO did not report the TaoBench server PID" >&2
  exit 1
fi
server_pid="$(<"${OUT_DIR}/server.pid")"

for _ in $(seq 1 120); do
  if nc -z 127.0.0.1 "${PORT}"; then
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    echo "TaoBench server exited before opening port ${PORT}" >&2
    exit 1
  fi
  sleep 1
done
if ! nc -z 127.0.0.1 "${PORT}"; then
  echo "TaoBench server did not become ready" >&2
  exit 1
fi
printf '%s\n' \
  "server_ready=$(date --iso-8601=ns)" \
  "server_pid=${server_pid}" \
  "trace_launch_ready=$(date --iso-8601=ns)" \
  >>"${OUT_DIR}/alignment-events.txt"

printf 'client_start=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
(
  while kill -0 "${server_pid}" 2>/dev/null; do
    if grep -q '^execution phase' "${OUT_DIR}/logs/client.log" 2>/dev/null; then
      printf 'execution_phase=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
      exit 0
    fi
    sleep 0.05
  done
) &
client_phase_monitor_pid=$!

python3 -u "${TAO_PACKAGE_DIR}/run.py" client \
  --server-hostname localhost \
  --server-memsize 4 \
  --server-port-number "${PORT}" \
  --clients-per-thread "${CLIENTS_PER_THREAD}" \
  --warmup-time "${WARMUP_SECONDS}" \
  --wait-after-warmup 0 \
  --test-time "${TEST_SECONDS}" \
  --disable-tls 0 \
  --real \
  >"${OUT_DIR}/logs/client.log" 2>&1
printf 'client_end=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
wait "${client_phase_monitor_pid}" 2>/dev/null || true
client_phase_monitor_pid=""

stop_pid "${server_pid}"
server_pid=""
wait "${drrun_pid}" 2>/dev/null || true
drrun_pid=""
wait "${trace_monitor_pid}" 2>/dev/null || true
trace_monitor_pid=""
wait "${trace_activation_monitor_pid}" 2>/dev/null || true
trace_activation_monitor_pid=""
stop_pid "${permission_monitor_pid}"
permission_monitor_pid=""

if ! grep -q 'execution phase' "${OUT_DIR}/logs/client.log" || \
    ! grep -Eq 'threads:[[:space:]]+[1-9][0-9]* ops' "${OUT_DIR}/logs/client.log" || \
    ! grep -Eq '(fast_qps|slow_qps) = [1-9][0-9]*([.][0-9]+)?' "${OUT_DIR}/logs/server.log"; then
  echo "TaoBench client did not complete both official phases" >&2
  exit 1
fi
if grep -qiE 'connection refused|error:|failed to connect' "${OUT_DIR}/logs/client.log"; then
  echo "TaoBench client logged a connection failure" >&2
  exit 1
fi

mapfile -t active_dr_dirs < <(
  find "${RAW_TRACE_DIR}" -type f -name '*.raw.*' -size +1024c -print |
    sed 's|/raw/.*$||' |
    sort -u
)
if [[ "${#active_dr_dirs[@]}" -ne 1 ]]; then
  echo "Expected one substantive TaoBench trace root, found ${#active_dr_dirs[@]}" >&2
  exit 1
fi
if ! grep -q '^trace_window_enabled=' "${OUT_DIR}/alignment-events.txt"; then
  echo "The delayed TaoBench trace window did not activate" >&2
  exit 1
fi
execution_line="$(grep -n '^execution_phase=' "${OUT_DIR}/alignment-events.txt" | cut -d: -f1)"
trace_line="$(grep -n '^trace_window_enabled=' "${OUT_DIR}/alignment-events.txt" | cut -d: -f1)"
client_end_line="$(grep -n '^client_end=' "${OUT_DIR}/alignment-events.txt" | cut -d: -f1)"
if (( execution_line >= trace_line || trace_line >= client_end_line )); then
  echo "TaoBench trace window did not fall inside the execution phase" >&2
  exit 1
fi

readonly dr_dir="${active_dr_dirs[0]}"
readonly raw_dir="${dr_dir}/raw"
test -s "${raw_dir}/modules.log"
first_raw_write="$(find "${raw_dir}" -type f -name '*.raw.*' \
  -printf '%T@ %TY-%Tm-%TdT%TH:%TM:%TS%Tz %p\n' | sort -n | sed -n '1p')"
printf 'first_raw_write=%s\n' "${first_raw_write}" >>"${OUT_DIR}/alignment-events.txt"

"${DR_DIR}/clients/bin64/drraw2trace" \
  -jobs 20 \
  -indir "${raw_dir}" \
  -chunk_instr_count 10000000 \
  >"${OUT_DIR}/logs/raw2trace.log" 2>&1

readonly trace_dir="${dr_dir}/trace"
mapfile -t trace_zips < <(find "${trace_dir}" -type f -name '*.trace.zip' | sort)
if [[ "${#trace_zips[@]}" -lt 1 ]]; then
  echo "No converted TaoBench trace ZIP found" >&2
  exit 1
fi

: >"${OUT_DIR}/logs/unzip-test.log"
for trace_zip in "${trace_zips[@]}"; do
  unzip -t "${trace_zip}" >>"${OUT_DIR}/logs/unzip-test.log"
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

mv "${RAW_TRACE_DIR}" "${OUT_DIR}/raw_trace"
rmdir "${STAGING_DIR}"
readonly final_trace_dir="${OUT_DIR}/raw_trace/$(basename "${dr_dir}")/trace"

printf '%s\n' \
  "status=trace_valid" \
  "target=tao_bench_server" \
  "trace_method=launch_with_delayed_instruction_window" \
  "trace_dir=${final_trace_dir}" \
  "trace_zip_count=${#trace_zips[@]}" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "fetched_instructions=${fetched_instructions}" \
  "warmup_seconds=${WARMUP_SECONDS}" \
  "test_seconds=${TEST_SECONDS}" \
  "clients_per_thread=${CLIENTS_PER_THREAD}" \
  "tls=enabled_private_openssl" \
  "alignment_evidence=${OUT_DIR}/alignment-events.txt" \
  >"${OUT_DIR}/pilot-summary.txt"

cat "${OUT_DIR}/pilot-summary.txt"
