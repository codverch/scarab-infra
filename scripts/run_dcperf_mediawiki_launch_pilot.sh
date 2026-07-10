#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  exec sudo --preserve-env=DCPERF_ROOT,TRACE_AFTER_INSTRUCTIONS,TRACE_INSTRUCTIONS,MEDIAWIKI_DURATION,MEDIAWIKI_TIMEOUT,HARNESS_STARTUP_DELAY,DR_EXTRA_OPS,WRAPPER_HHVM_EXTRA,DR_DIR_OVERRIDE,DRMEMTRACE_CLIENT_OVERRIDE \
    "$0" "$@"
fi

readonly BENCH_USER="${SUDO_USER:-${USER}}"
readonly ROOT_DIR="${DCPERF_ROOT:-/proj/datacntr-effcy-PG0/${BENCH_USER}/hpca2027_dcperf}"
readonly DCPERF_DIR="${ROOT_DIR}/src/DCPerf"
readonly SCARAB_DIR="${ROOT_DIR}/src/scarab-hpca2027-characterization"
readonly BUILD_DIR="${SCARAB_DIR}/src/build/opt"
readonly DR_DIR="${DR_DIR_OVERRIDE:-${BUILD_DIR}/deps/dynamorio}"
readonly DRMEMTRACE_CLIENT="${DRMEMTRACE_CLIENT_OVERRIDE:-${DR_DIR}/clients/lib64/release/libdrmemtrace.so}"
readonly REAL_HHVM="/usr/local/hphpi/legacy/bin/hhvm"
readonly WRK_BIN="${DCPERF_DIR}/benchmarks/oss_performance_mediawiki/wrk/wrk"
readonly OUT_DIR="${1:-${ROOT_DIR}/traces/mediawiki_launch_viability_20260709}"
# Phase-1 default: astronomically large delay = counting mode only, no tracing.
readonly TRACE_AFTER_INSTRUCTIONS="${TRACE_AFTER_INSTRUCTIONS:-200000000000000}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-1000000}"
readonly DURATION="${MEDIAWIKI_DURATION:-2m}"
readonly TIMEOUT="${MEDIAWIKI_TIMEOUT:-3m}"
readonly STARTUP_DELAY="${HARNESS_STARTUP_DELAY:-60}"
readonly CLIENT_THREADS="$((2 * $(nproc)))"

benchmark_pid=""

export LD_LIBRARY_PATH="/opt/local/hhvm-3.30/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

cleanup() {
  if [[ -n "${benchmark_pid}" ]] && kill -0 "${benchmark_pid}" 2>/dev/null; then
    kill -INT "${benchmark_pid}" 2>/dev/null || true
    sleep 2
    kill -TERM "${benchmark_pid}" 2>/dev/null || true
  fi
  pkill -f 'drmemtrace.*hhvm' 2>/dev/null || true
  if [[ -d "${OUT_DIR}" ]]; then
    chown -R "${BENCH_USER}:$(id -gn "${BENCH_USER}")" "${OUT_DIR}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for path in "${DR_DIR}/bin64/drrun" "${DRMEMTRACE_CLIENT}" "${REAL_HHVM}" "${WRK_BIN}"; do
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
chmod 777 "${OUT_DIR}/raw_trace"

readonly WRAPPER="${OUT_DIR}/hhvm-dr-wrapper.sh"
cat >"${WRAPPER}" <<EOF
#!/usr/bin/env bash
# Route only the HHVM *server* invocation under DynamoRIO drmemtrace launch mode.
if [[ " \$* " == *" -m server "* ]]; then
  printf 'dr_launch_server=%s\\n' "\$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
  exec "${DR_DIR}/bin64/drrun" \\
    -ops "-no_follow_children ${DR_EXTRA_OPS:-}" \\
    -c "${DRMEMTRACE_CLIENT}" \\
    -outdir "${OUT_DIR}/raw_trace" \\
    -offline \\
    -no_split_windows \\
    -trace_after_instrs "${TRACE_AFTER_INSTRUCTIONS}" \\
    -trace_for_instrs "${TRACE_INSTRUCTIONS}" \\
    -- "${REAL_HHVM}" "\$@" ${WRAPPER_HHVM_EXTRA:-}
fi
exec "${REAL_HHVM}" "\$@"
EOF
chmod 755 "${WRAPPER}"

cd "${DCPERF_DIR}"
printf 'benchmark_start=%s\n' "$(date --iso-8601=ns)" >"${OUT_DIR}/alignment-events.txt"

cd "${DCPERF_DIR}/oss-performance"
HHVM_DISABLE_NUMA=1 "${REAL_HHVM}" \
  -vEval.ProfileHWEnable=0 \
  perf.php \
  --nginx nginx \
  --wrk "${WRK_BIN}" \
  --hhvm "${WRAPPER}" \
  --db-username=root \
  --db-password=password \
  --memcached=/usr/local/memcached/bin/memcached \
  --memcached-threads 8 \
  --client-threads "${CLIENT_THREADS}" \
  --server-threads 200 \
  --scale-out 1 \
  "--delay-check-health=${STARTUP_DELAY}" \
  "--delay-php-startup=${STARTUP_DELAY}" \
  --hhvm-extra-arguments='-vEval.ProfileHWEnable=0' \
  --mediawiki-mlp \
  "--client-duration=${DURATION}" \
  "--client-timeout=${TIMEOUT}" \
  --run-as-root \
  --skip-sanity-check \
  --i-am-not-benchmarking \
  >"${OUT_DIR}/logs/benchmark.log" 2>&1 &
benchmark_pid=$!

if ! wait "${benchmark_pid}"; then
  echo "MediaWiki benchmark failed; see ${OUT_DIR}/logs/benchmark.log" >&2
  exit 1
fi
benchmark_pid=""
printf 'benchmark_end=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

grep -E 'Server warmed|Starting HHVM engine|Starting execution|Collecting results|Skipping sanity' \
  "${OUT_DIR}/logs/benchmark.log" >>"${OUT_DIR}/alignment-events.txt" || true

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

printf '%s\n' \
  "status=launch_viability_ok" \
  "attach_method=launch_mode_wrapper_counting_delay" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "successful_requests=${successful_requests}" \
  "client_duration=${DURATION}" \
  "alignment_evidence=${OUT_DIR}/alignment-events.txt" \
  >"${OUT_DIR}/pilot-summary.txt"

cat "${OUT_DIR}/pilot-summary.txt"
