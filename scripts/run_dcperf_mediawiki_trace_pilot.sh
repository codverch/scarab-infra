#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  exec sudo --preserve-env=DCPERF_ROOT,TRACE_AFTER_INSTRUCTIONS,TRACE_INSTRUCTIONS,MEDIAWIKI_DURATION,MEDIAWIKI_TIMEOUT \
    "$0" "$@"
fi

readonly BENCH_USER="${SUDO_USER:-${USER}}"
readonly ROOT_DIR="${DCPERF_ROOT:-/proj/datacntr-effcy-PG0/${BENCH_USER}/hpca2027_dcperf}"
readonly DCPERF_DIR="${ROOT_DIR}/src/DCPerf"
readonly SCARAB_DIR="${ROOT_DIR}/src/scarab-hpca2027-characterization"
readonly BUILD_DIR="${SCARAB_DIR}/src/build/opt"
readonly DR_DIR="${BUILD_DIR}/deps/dynamorio"
readonly DRMEMTRACE_CLIENT="${DR_DIR}/clients/lib64/release/libdrmemtrace.so"
readonly REAL_HHVM="/usr/local/hphpi/legacy/bin/hhvm"
readonly WRK_BIN="${DCPERF_DIR}/benchmarks/oss_performance_mediawiki/wrk/wrk"
readonly OUT_DIR="${1:-${ROOT_DIR}/traces/mediawiki_mlp_pilot_1m}"
readonly TRACE_AFTER_INSTRUCTIONS="${TRACE_AFTER_INSTRUCTIONS:-0}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-1000000}"
readonly DURATION="${MEDIAWIKI_DURATION:-2m}"
readonly TIMEOUT="${MEDIAWIKI_TIMEOUT:-3m}"
readonly CLIENT_THREADS="$((2 * $(nproc)))"

benchmark_pid=""

cleanup() {
  if [[ -n "${benchmark_pid}" ]] && kill -0 "${benchmark_pid}" 2>/dev/null; then
    kill -INT "${benchmark_pid}" 2>/dev/null || true
    sleep 2
    kill -TERM "${benchmark_pid}" 2>/dev/null || true
  fi
  if [[ -s "${OUT_DIR}/drrun.pid" ]]; then
    drrun_pid="$(<"${OUT_DIR}/drrun.pid")"
    kill -TERM "${drrun_pid}" 2>/dev/null || true
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
  "${REAL_HHVM}" \
  "${WRK_BIN}"; do
  if [[ ! -x "${path}" ]]; then
    echo "Missing executable: ${path}" >&2
    exit 1
  fi
done

if pgrep -x hhvm >/dev/null; then
  echo "An HHVM process is already running" >&2
  exit 1
fi

if [[ -e "${OUT_DIR}" ]]; then
  echo "Refusing to overwrite existing output: ${OUT_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}/raw_trace" "${OUT_DIR}/logs"

readonly ATTACH_HOOK="${OUT_DIR}/attach-after-warmup.sh"
cat >"${ATTACH_HOOK}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

mapfile -t server_pids < <(pgrep -f '^/.*/hhvm -m server( |$)')
if [[ "\${#server_pids[@]}" -lt 1 ]]; then
  echo "No native HHVM server process found" >&2
  exit 1
fi

server_pid=""
for candidate in "\${server_pids[@]}"; do
  candidate_ppid="\$(awk '/^PPid:/ {print \$2}' "/proc/\${candidate}/status")"
  is_child=0
  for other in "\${server_pids[@]}"; do
    if [[ "\${candidate_ppid}" == "\${other}" ]]; then
      is_child=1
      break
    fi
  done
  if (( is_child == 0 )); then
    if [[ -n "\${server_pid}" ]]; then
      echo "Multiple root HHVM server processes found" >&2
      exit 1
    fi
    server_pid="\${candidate}"
  fi
done
if [[ -z "\${server_pid}" ]]; then
  echo "Could not identify the root HHVM server process" >&2
  exit 1
fi

printf 'attach_hook_start=%s\\n' "\$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
printf 'server_pid_candidates=%s\\n' "\${server_pids[*]}" >>"${OUT_DIR}/alignment-events.txt"
printf 'server_pid=%s\\n' "\${server_pid}" >>"${OUT_DIR}/alignment-events.txt"

"${DR_DIR}/bin64/drrun" \
  -ops "-no_follow_children" \
  -attach "\${server_pid}" \
  -c "${DRMEMTRACE_CLIENT}" \
  -outdir "${OUT_DIR}/raw_trace" \
  -offline \
  -no_split_windows \
  -trace_after_instrs "${TRACE_AFTER_INSTRUCTIONS}" \
  -trace_for_instrs "${TRACE_INSTRUCTIONS}" \
  >"${OUT_DIR}/logs/attach-drrun.log" 2>&1 &
drrun_pid=\$!
printf '%s\\n' "\${drrun_pid}" >"${OUT_DIR}/drrun.pid"

for _ in \$(seq 1 100); do
  if find "${OUT_DIR}/raw_trace" -maxdepth 1 -type d -name 'drmemtrace.*.dir' -print -quit | grep -q .; then
    printf 'attach_ready=%s\\n' "\$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
    exit 0
  fi
  sleep 0.1
done

echo "DynamoRIO attach did not create a trace root within 10 seconds" >&2
exit 1
EOF
chmod 755 "${ATTACH_HOOK}"

cd "${DCPERF_DIR}"
printf 'benchmark_start=%s\n' "$(date --iso-8601=ns)" >"${OUT_DIR}/alignment-events.txt"

"${DCPERF_DIR}/packages/mediawiki/run.sh" \
  -r "${REAL_HHVM}" \
  -n nginx \
  -L wrk \
  -s "${WRK_BIN}" \
  -R 1 \
  -c "${CLIENT_THREADS}" \
  -- \
  --mediawiki-mlp \
  "--client-duration=${DURATION}" \
  "--client-timeout=${TIMEOUT}" \
  --run-as-root \
  "--exec-after-warmup=${ATTACH_HOOK}" \
  --i-am-not-benchmarking \
  >"${OUT_DIR}/logs/benchmark.log" 2>&1 &
benchmark_pid=$!

if ! wait "${benchmark_pid}"; then
  echo "MediaWiki benchmark failed; see ${OUT_DIR}/logs/benchmark.log" >&2
  exit 1
fi
benchmark_pid=""
printf 'benchmark_end=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

if [[ -s "${OUT_DIR}/drrun.pid" ]]; then
  drrun_pid="$(<"${OUT_DIR}/drrun.pid")"
  for _ in $(seq 1 60); do
    if ! kill -0 "${drrun_pid}" 2>/dev/null; then
      break
    fi
    sleep 1
  done
fi

if grep -qE 'Fatal error:|InvariantException|benchmark failed' "${OUT_DIR}/logs/benchmark.log"; then
  echo "MediaWiki harness logged a fatal error" >&2
  exit 1
fi
if ! grep -q 'Wrk RPS' "${OUT_DIR}/logs/benchmark.log"; then
  echo "MediaWiki harness did not report Wrk RPS" >&2
  exit 1
fi
successful_requests="$(awk -F': ' '/"Wrk successful requests"/ {gsub(/[, ]/, "", $2); print $2; exit}' \
  "${OUT_DIR}/logs/benchmark.log")"
if [[ ! "${successful_requests}" =~ ^[0-9]+$ ]] || (( successful_requests == 0 )); then
  echo "MediaWiki harness reported ${successful_requests:-unknown} successful requests" >&2
  exit 1
fi

mapfile -t all_dr_dirs < <(find "${OUT_DIR}/raw_trace" -maxdepth 1 -type d -name 'drmemtrace.*.dir')
mapfile -t active_dr_dirs < <(
  find "${OUT_DIR}/raw_trace" -type f -name '*.raw.*' -size +1024c -print |
    sed 's|/raw/.*$||' |
    sort -u
)
if [[ "${#active_dr_dirs[@]}" -ne 1 ]]; then
  echo "Expected one substantive HHVM trace root, found ${#active_dr_dirs[@]}" >&2
  exit 1
fi

readonly dr_dir="${active_dr_dirs[0]}"
readonly raw_dir="${dr_dir}/raw"
test -s "${raw_dir}/modules.log"
raw_trace_file="$(find "${raw_dir}" -type f -name '*.raw.*' -size +0c -print -quit)"
if [[ -z "${raw_trace_file}" ]]; then
  echo "No non-empty raw trace file was generated" >&2
  exit 1
fi

first_raw_write="$(find "${raw_dir}" -type f -name '*.raw.*' -printf '%T@ %TY-%Tm-%TdT%TH:%TM:%TS%Tz %p\n' | sort -n | head -n 1)"
printf 'first_raw_write=%s\n' "${first_raw_write}" >>"${OUT_DIR}/alignment-events.txt"
grep -E 'Server warmed|Starting HHVM engine|Starting execution|Collecting results' \
  "${OUT_DIR}/logs/benchmark.log" >>"${OUT_DIR}/alignment-events.txt" || true

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
  "target=mediawiki_mlp_hhvm_server" \
  "attach_method=post_warmup_direct_drmemtrace_client" \
  "trace_dir=${trace_dir}" \
  "trace_zip_count=${#trace_zips[@]}" \
  "trace_root_count=${#all_dr_dirs[@]}" \
  "substantive_trace_root_count=${#active_dr_dirs[@]}" \
  "modules_dir=${raw_dir}" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "fetched_instructions=${fetched_instructions}" \
  "trace_counter=trace_for_instrs" \
  "chunk_count=${chunk_count}" \
  "successful_requests=${successful_requests}" \
  "client_duration=${DURATION}" \
  "alignment_evidence=${OUT_DIR}/alignment-events.txt" \
  >"${OUT_DIR}/pilot-summary.txt"

cat "${OUT_DIR}/pilot-summary.txt"
