#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  exec sudo --preserve-env=DCPERF_ROOT,TRACE_AFTER_INSTRUCTIONS,TRACE_INSTRUCTIONS,DJANGO_DURATION,DJANGO_ITERATIONS,TRACE_HARAKIRI,DR_DIR_OVERRIDE,DRMEMTRACE_CLIENT_OVERRIDE \
    "$0" "$@"
fi

readonly BENCH_USER="${SUDO_USER:-${USER}}"
readonly ROOT_DIR="${DCPERF_ROOT:-/proj/datacntr-effcy-PG0/${BENCH_USER}/hpca2027_dcperf}"
readonly DCPERF_DIR="${ROOT_DIR}/src/DCPerf"
readonly SCARAB_DIR="${ROOT_DIR}/src/scarab-hpca2027-characterization"
readonly BUILD_DIR="${SCARAB_DIR}/src/build/opt"
readonly DR_DIR="${DR_DIR_OVERRIDE:-${BUILD_DIR}/deps/dynamorio}"
readonly DRMEMTRACE_CLIENT="${DRMEMTRACE_CLIENT_OVERRIDE:-${DR_DIR}/clients/lib64/release/libdrmemtrace.so}"
readonly DJANGO_DIR="${DCPERF_DIR}/benchmarks/django_workload"
readonly DJANGO_APP_DIR="${DJANGO_DIR}/django-workload/django-workload"
readonly CLIENT_DIR="${DJANGO_DIR}/django-workload/client"
readonly RUNNER="${DJANGO_DIR}/bin/run.sh"
readonly OUT_DIR="${1:-${ROOT_DIR}/traces/django_launch_viability_20260709}"
readonly TRACE_AFTER_INSTRUCTIONS="${TRACE_AFTER_INSTRUCTIONS:-200000000000000}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-1000000}"
readonly CLIENT_DURATION="${DJANGO_DURATION:-30S}"
readonly ITERATIONS="${DJANGO_ITERATIONS:-1}"
readonly SERVER_WORKERS="$(nproc)"
readonly CLIENT_WORKERS="$(((12 * $(nproc) + 5) / 10))"

db_pid=""
uwsgi_launch_pid=""

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
    for _ in $(seq 1 30); do
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
  [[ -n "${uwsgi_launch_pid}" ]] && stop_pid "${uwsgi_launch_pid}"
  [[ -n "${db_pid}" ]] && stop_pid "${db_pid}"
  if [[ -d "${OUT_DIR}" ]]; then
    chown -R "${BENCH_USER}:$(id -gn "${BENCH_USER}")" "${OUT_DIR}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for path in "${DR_DIR}/bin64/drrun" "${DRMEMTRACE_CLIENT}" "${RUNNER}"; do
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
chmod 777 "${OUT_DIR}/raw_trace"
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

# Replicate the runner's `-r server` branch exactly, but launch uWSGI under
# DynamoRIO drmemtrace (launch mode, forks followed so workers are traced).
cd "${DJANGO_APP_DIR}"
export LD_LIBRARY_PATH="${DJANGO_APP_DIR}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
DJANGO_SETTINGS_MODULE=cluster_settings ./venv/bin/django-admin flush \
  >"${OUT_DIR}/logs/django-admin.log" 2>&1 || true
DJANGO_SETTINGS_MODULE=cluster_settings ./venv/bin/django-admin setup \
  >>"${OUT_DIR}/logs/django-admin.log" 2>&1

printf 'dr_launch_uwsgi=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
"${DR_DIR}/bin64/drrun" \
  -c "${DRMEMTRACE_CLIENT}" \
  -outdir "${OUT_DIR}/raw_trace" \
  -offline \
  -no_split_windows \
  -trace_after_instrs "${TRACE_AFTER_INSTRUCTIONS}" \
  -trace_for_instrs "${TRACE_INSTRUCTIONS}" \
  -- ./venv/bin/uwsgi \
  --ini uwsgi.ini \
  -H "${DJANGO_APP_DIR}/venv" \
  --safe-pidfile "${DJANGO_DIR}/uwsgi.pid" \
  --workers "${SERVER_WORKERS}" \
  --harakiri "${TRACE_HARAKIRI:-600}" \
  >"${OUT_DIR}/logs/server.log" 2>&1 &
uwsgi_launch_pid=$!

for _ in $(seq 1 600); do
  if nc -z 127.0.0.1 8000 && [[ -s "${DJANGO_DIR}/uwsgi.pid" ]]; then
    break
  fi
  if ! kill -0 "${uwsgi_launch_pid}" 2>/dev/null; then
    echo "DR-launched uWSGI exited before opening port 8000" >&2
    exit 1
  fi
  sleep 1
done
if ! nc -z 127.0.0.1 8000 || [[ ! -s "${DJANGO_DIR}/uwsgi.pid" ]]; then
  echo "DR-launched Django server did not become ready within 10 minutes" >&2
  exit 1
fi
printf 'django_port_open=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

# Port binds before the app loads (and loads slowly under DR); require a real
# HTTP response from the app before declaring readiness.
app_ready=0
for _ in $(seq 1 180); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 120 "http://127.0.0.1:8000/feed_timeline" 2>/dev/null || true)
  if [[ "${code}" =~ ^(200|301|302)$ ]]; then
    app_ready=1
    break
  fi
  sleep 5
done
if (( app_ready == 0 )); then
  echo "DR-launched Django app did not serve an HTTP response within 15 minutes (last code: ${code:-none})" >&2
  exit 1
fi
printf 'django_ready=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"

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

uwsgi_master_pid="$(<"${DJANGO_DIR}/uwsgi.pid")"
kill -QUIT "${uwsgi_master_pid}" 2>/dev/null || true
printf 'uwsgi_graceful_stop=%s\n' "$(date --iso-8601=ns)" >>"${OUT_DIR}/alignment-events.txt"
for _ in $(seq 1 120); do
  if ! kill -0 "${uwsgi_launch_pid}" 2>/dev/null; then
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

dir_count="$(find "${OUT_DIR}/raw_trace" -maxdepth 1 -type d -name 'drmemtrace.*.dir' | wc -l)"
nonempty_modules="$(find "${OUT_DIR}/raw_trace" -type f -name 'modules.log' -size +0c | wc -l)"
if (( nonempty_modules == 0 )); then
  echo "No drmemtrace process produced a non-empty modules.log" >&2
  exit 1
fi

printf '%s\n' \
  "status=launch_viability_ok" \
  "attach_method=launch_mode_uwsgi_forks_followed" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "successful_transactions=${successful_transactions}" \
  "server_workers=${SERVER_WORKERS}" \
  "trace_root_count=${dir_count}" \
  "nonempty_modules_count=${nonempty_modules}" \
  "duration=${CLIENT_DURATION}" \
  "alignment_evidence=${OUT_DIR}/alignment-events.txt" \
  >"${OUT_DIR}/pilot-summary.txt"

cat "${OUT_DIR}/pilot-summary.txt"
