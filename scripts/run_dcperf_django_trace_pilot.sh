#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  exec sudo --preserve-env=DCPERF_ROOT,TRACE_INSTRUCTIONS,DJANGO_DURATION,DJANGO_ITERATIONS \
    "$0" "$@"
fi

readonly BENCH_USER="${SUDO_USER:-${USER}}"
readonly ROOT_DIR="${DCPERF_ROOT:-/proj/datacntr-effcy-PG0/${BENCH_USER}/hpca2027_dcperf}"
readonly DCPERF_DIR="${ROOT_DIR}/src/DCPerf"
readonly SCARAB_DIR="${ROOT_DIR}/src/scarab-hpca2027-characterization"
readonly BUILD_DIR="${SCARAB_DIR}/src/build/opt"
readonly DR_DIR="${BUILD_DIR}/deps/dynamorio"
readonly DRMEMTRACE_CLIENT="${DR_DIR}/clients/lib64/release/libdrmemtrace.so"
readonly DJANGO_DIR="${DCPERF_DIR}/benchmarks/django_workload"
readonly DJANGO_APP_DIR="${DJANGO_DIR}/django-workload/django-workload"
readonly CLIENT_DIR="${DJANGO_DIR}/django-workload/client"
readonly RUNNER="${DJANGO_DIR}/bin/run.sh"
readonly OUT_DIR="${1:-${ROOT_DIR}/traces/django_worker_pilot_1m}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-1000000}"
readonly CLIENT_DURATION="${DJANGO_DURATION:-30S}"
readonly ITERATIONS="${DJANGO_ITERATIONS:-1}"
readonly SERVER_WORKERS="$(nproc)"
readonly CLIENT_WORKERS="$(((12 * $(nproc) + 5) / 10))"

db_pid=""
server_pid=""
drrun_pid=""

stop_pid() {
  local pid="$1"

  if ! kill -0 "${pid}" 2>/dev/null; then
    return
  fi
  kill -INT "${pid}" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return
    fi
    sleep 1
  done
  kill -TERM "${pid}" 2>/dev/null || true
}

cleanup() {
  if [[ -s "${DJANGO_DIR}/uwsgi.pid" ]]; then
    uwsgi_pid="$(<"${DJANGO_DIR}/uwsgi.pid")"
    kill -QUIT "${uwsgi_pid}" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "${uwsgi_pid}" 2>/dev/null || break
      sleep 1
    done
  fi
  if [[ -s "${DJANGO_DIR}/cassandra.pid" ]]; then
    stop_pid "$(<"${DJANGO_DIR}/cassandra.pid")"
  fi
  while read -r cassandra_pid; do
    [[ -n "${cassandra_pid}" ]] && stop_pid "${cassandra_pid}"
  done < <(pgrep -f 'org.apache.cassandra.service.CassandraDaemon' 2>/dev/null || true)
  while read -r memcached_pid; do
    [[ -n "${memcached_pid}" ]] && stop_pid "${memcached_pid}"
  done < <(pgrep -f '^/usr/bin/memcached .* -p 11811( |$)' 2>/dev/null || true)
  [[ -n "${server_pid}" ]] && stop_pid "${server_pid}"
  [[ -n "${db_pid}" ]] && stop_pid "${db_pid}"
  [[ -n "${drrun_pid}" ]] && stop_pid "${drrun_pid}"
  if [[ -d "${OUT_DIR}" ]]; then
    chown -R "${BENCH_USER}:$(id -gn "${BENCH_USER}")" "${OUT_DIR}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for path in \
  "${DR_DIR}/bin64/drrun" \
  "${DR_DIR}/clients/bin64/drraw2trace" \
  "${DRMEMTRACE_CLIENT}" \
  "${RUNNER}"; do
  if [[ ! -x "${path}" ]]; then
    echo "Missing executable: ${path}" >&2
    exit 1
  fi
done

if pgrep -f 'uwsgi.*django-workload' >/dev/null || pgrep -f 'org.apache.cassandra.service.CassandraDaemon' >/dev/null; then
  echo "A Django uWSGI or Cassandra process is already running" >&2
  exit 1
fi
if nc -z 127.0.0.1 8000 || nc -z 127.0.0.1 9042 || nc -z 127.0.0.1 11811; then
  echo "A required Django port (8000, 9042, or 11811) is already in use" >&2
  exit 1
fi
if [[ -e "${OUT_DIR}" ]]; then
  echo "Refusing to overwrite existing output: ${OUT_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}/raw_trace" "${OUT_DIR}/logs"
printf 'pilot_start=%s\n' "$(date --iso-8601=ns)" >"${OUT_DIR}/alignment-events.txt"

"${RUNNER}" -r db -b 127.0.0.1 >"${OUT_DIR}/logs/db.log" 2>&1 &
db_pid=$!
for _ in $(seq 1 180); do
  if nc -z 127.0.0.1 9042; then
    break
  fi
  if ! kill -0 "${db_pid}" 2>/dev/null; then
    echo "Cassandra exited before opening port 9042" >&2
    exit 1
  fi
  sleep 1
done
if ! nc -z 127.0.0.1 9042; then
  echo "Cassandra did not open port 9042" >&2
  exit 1
fi
printf 'cassandra_ready=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

"${RUNNER}" -r server -c 127.0.0.1 -w "${SERVER_WORKERS}" \
  >"${OUT_DIR}/logs/server.log" 2>&1 &
server_pid=$!
for _ in $(seq 1 180); do
  if nc -z 127.0.0.1 8000 && [[ -s "${DJANGO_DIR}/uwsgi.pid" ]]; then
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    echo "Django server exited before opening port 8000" >&2
    exit 1
  fi
  sleep 1
done
if ! nc -z 127.0.0.1 8000 || [[ ! -s "${DJANGO_DIR}/uwsgi.pid" ]]; then
  echo "Django server did not become ready" >&2
  exit 1
fi

readonly uwsgi_master_pid="$(<"${DJANGO_DIR}/uwsgi.pid")"
worker_pids=()
for _ in $(seq 1 100); do
  mapfile -t worker_pids < <(pgrep -P "${uwsgi_master_pid}" | sort -n)
  if [[ "${#worker_pids[@]}" -gt 0 ]]; then
    break
  fi
  sleep 0.1
done
if [[ "${#worker_pids[@]}" -lt 1 ]]; then
  echo "No uWSGI worker process found" >&2
  exit 1
fi
readonly worker_pid="${worker_pids[0]}"
printf '%s\n' \
  "django_ready=$(date --iso-8601=ns)" \
  "uwsgi_master_pid=${uwsgi_master_pid}" \
  "uwsgi_worker_pids=${worker_pids[*]}" \
  "traced_worker_pid=${worker_pid}" \
  >>"${OUT_DIR}/alignment-events.txt"

"${DR_DIR}/bin64/drrun" \
  -verbose \
  -ops "-no_follow_children" \
  -attach "${worker_pid}" \
  -c "${DRMEMTRACE_CLIENT}" \
  -outdir "${OUT_DIR}/raw_trace" \
  -offline \
  -no_split_windows \
  -trace_for_instrs "${TRACE_INSTRUCTIONS}" \
  >"${OUT_DIR}/logs/attach-drrun.log" 2>&1 &
drrun_pid=$!

for _ in $(seq 1 100); do
  if find "${OUT_DIR}/raw_trace" -maxdepth 1 -type d -name 'drmemtrace.*.dir' -print -quit | grep -q .; then
    break
  fi
  if ! kill -0 "${drrun_pid}" 2>/dev/null; then
    echo "DynamoRIO attach exited before creating a trace root" >&2
    exit 1
  fi
  sleep 0.1
done
if ! find "${OUT_DIR}/raw_trace" -maxdepth 1 -type d -name 'drmemtrace.*.dir' -print -quit | grep -q .; then
  echo "DynamoRIO attach did not create a trace root" >&2
  exit 1
fi
printf 'attach_ready=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

sed -e 's#//.*:8000#//127.0.0.1:8000#g' \
  "${CLIENT_DIR}/urls_template.txt" >"${CLIENT_DIR}/urls_template.txt.tmp"
mv -f "${CLIENT_DIR}/urls_template.txt.tmp" "${CLIENT_DIR}/urls_template.txt"

# shellcheck disable=SC1091
source "${DJANGO_APP_DIR}/venv/bin/activate"
cd "${CLIENT_DIR}"
./gen-urls-file

printf 'client_start=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
WORKERS="${CLIENT_WORKERS}" \
  DURATION="${CLIENT_DURATION}" \
  LOG="${OUT_DIR}/siege.log" \
  SOURCE=urls.txt \
  python3 ./run-siege -i "${ITERATIONS}" -r 0 \
  >"${OUT_DIR}/logs/client.log" 2>&1
printf 'client_end=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

kill -QUIT "${uwsgi_master_pid}" 2>/dev/null || true
printf 'uwsgi_graceful_stop=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
for _ in $(seq 1 60); do
  if ! kill -0 "${drrun_pid}" 2>/dev/null; then
    break
  fi
  sleep 1
done

successful_transactions="$(awk '/^Successful transactions:/ {print $3; exit}' \
  "${OUT_DIR}/logs/client.log")"
if [[ ! "${successful_transactions}" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
    ! awk -v value="${successful_transactions}" 'BEGIN {exit !(value > 0)}'; then
  echo "Django client reported ${successful_transactions:-unknown} successful transactions" >&2
  exit 1
fi

mapfile -t active_dr_dirs < <(
  find "${OUT_DIR}/raw_trace" -type f -name '*.raw.*' -size +1024c -print |
    sed 's|/raw/.*$||' |
    sort -u
)
if [[ "${#active_dr_dirs[@]}" -ne 1 ]]; then
  echo "Expected one substantive uWSGI trace root, found ${#active_dr_dirs[@]}" >&2
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
  echo "No converted uWSGI trace ZIP found" >&2
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
  "target=django_uwsgi_worker" \
  "attach_method=post_server_ready_direct_drmemtrace_client" \
  "traced_worker_pid=${worker_pid}" \
  "trace_dir=${trace_dir}" \
  "trace_zip_count=${#trace_zips[@]}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "fetched_instructions=${fetched_instructions}" \
  "chunk_count=${chunk_count}" \
  "successful_transactions=${successful_transactions}" \
  "duration=${CLIENT_DURATION}" \
  "iterations=${ITERATIONS}" \
  "alignment_evidence=${OUT_DIR}/alignment-events.txt" \
  >"${OUT_DIR}/pilot-summary.txt"

cat "${OUT_DIR}/pilot-summary.txt"
