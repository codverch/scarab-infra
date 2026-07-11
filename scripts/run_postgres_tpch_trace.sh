#!/usr/bin/env bash

set -euo pipefail

readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly ROOT="${DB_ROOT}/postgres-tpch"
readonly DATA_DIR="${ROOT}/data"
readonly QUERY_DIR="${ROOT}/queries"
readonly POSTGRES_IMAGE="postgres:16.10-bookworm"
readonly POSTGRES_CONTAINER="hpca-postgres-tpch"
readonly TRACE_CONTAINER="hpca-postgres-tpch-trace"
readonly DYNAMORIO_DIR="${DYNAMORIO_DIR:-/proj/datacntr-effcy-PG0/${USER}/hpca2027_dcperf/DynamoRIO-Linux-11.91.20634}"
readonly TRACE_AFTER_INSTRUCTIONS="${TRACE_AFTER_INSTRUCTIONS:-100000000}"
readonly TRACE_INSTRUCTIONS="${TRACE_INSTRUCTIONS:-120000000}"
readonly MIN_FETCHED_INSTRUCTIONS="${MIN_FETCHED_INSTRUCTIONS:-${TRACE_INSTRUCTIONS}}"
readonly SERVER_CPUS="${SERVER_CPUS:-0-23}"
readonly SNAPPY_LIB="${SNAPPY_LIB:-/lib/x86_64-linux-gnu/libsnappy.so.1}"

if [[ -x "${DYNAMORIO_DIR}/tools/bin64/drraw2trace" ]]; then
  readonly DRRAW2TRACE_RELATIVE="tools/bin64/drraw2trace"
elif [[ -x "${DYNAMORIO_DIR}/clients/bin64/drraw2trace" ]]; then
  readonly DRRAW2TRACE_RELATIVE="clients/bin64/drraw2trace"
else
  echo "Missing drraw2trace under ${DYNAMORIO_DIR}" >&2
  exit 1
fi

usage() {
  echo "Usage: run_postgres_tpch_trace.sh <scale-factor> <query> <output-dir>" >&2
}

[[ $# == 3 ]] || { usage; exit 2; }
readonly SCALE="$1"
readonly QUERY="$2"
readonly OUT_DIR="$3"
readonly PGDATA="${DATA_DIR}/postgres_sf${SCALE}"
readonly QUERY_FILE="${QUERY_DIR}/sf${SCALE}/q${QUERY}.sql"
readonly RAW_ROOT="${OUT_DIR}/raw_trace"

[[ "${QUERY}" =~ ^([1-9]|1[0-9]|2[0-2])$ ]] || { echo "Query must be 1-22" >&2; exit 2; }
[[ "${TRACE_AFTER_INSTRUCTIONS}" =~ ^[0-9]+$ ]] || { echo "Invalid trace delay" >&2; exit 2; }
[[ "${TRACE_INSTRUCTIONS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid trace length" >&2; exit 2; }
[[ "${MIN_FETCHED_INSTRUCTIONS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid fetched-instruction minimum" >&2; exit 2; }
(( MIN_FETCHED_INSTRUCTIONS <= TRACE_INSTRUCTIONS )) || {
  echo "Fetched-instruction minimum exceeds requested trace length" >&2
  exit 2
}
[[ "$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)" == "/dev/sdb" ]] || {
  echo "${DB_ROOT} is not mounted from /dev/sdb" >&2
  exit 1
}
test -f "${DATA_DIR}/.postgres_sf${SCALE}-ready"
test -s "${QUERY_FILE}"
test -x "${DYNAMORIO_DIR}/bin64/drrun"
test -x "${DYNAMORIO_DIR}/${DRRAW2TRACE_RELATIVE}"
test -r "${SNAPPY_LIB}"
[[ ! -e "${OUT_DIR}" ]] || { echo "Refusing to overwrite ${OUT_DIR}" >&2; exit 1; }
if pgrep -af 'drrun|drraw2trace' | grep -v 'pgrep -af' >/dev/null; then
  echo "A DynamoRIO job is already active" >&2
  exit 1
fi

cleanup() {
  docker rm -f "${TRACE_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker rm -f "${POSTGRES_CONTAINER}" "${TRACE_CONTAINER}" >/dev/null 2>&1 || true
mkdir -p "${RAW_ROOT}" "${OUT_DIR}/logs"
chmod 0777 "${RAW_ROOT}" "${OUT_DIR}/logs"
printf '%s\n' \
  "trace_start=$(date --iso-8601=ns)" \
  "scale_factor=${SCALE}" \
  "query=${QUERY}" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  >"${OUT_DIR}/alignment-events.txt"

set +e
docker run --rm --name "${TRACE_CONTAINER}" \
  --cpuset-cpus "${SERVER_CPUS}" \
  --user postgres \
  -v "${PGDATA}:/var/lib/postgresql/data" \
  -v "${QUERY_FILE}:/query.sql:ro" \
  -v "${DYNAMORIO_DIR}:/dynamorio:ro" \
  -v "${SNAPPY_LIB}:/dynamorio-compat/libsnappy.so.1:ro" \
  -v "${RAW_ROOT}:/trace-output" \
  -e LD_LIBRARY_PATH=/dynamorio-compat \
  "${POSTGRES_IMAGE}" \
  /dynamorio/bin64/drrun \
    -t drcachesim \
    -jobs 20 \
    -outdir /trace-output \
    -offline \
    -no_split_windows \
    -trace_after_instrs "${TRACE_AFTER_INSTRUCTIONS}" \
    -trace_for_instrs "${TRACE_INSTRUCTIONS}" \
    -exit_after_tracing "${TRACE_INSTRUCTIONS}" \
    -- /usr/lib/postgresql/16/bin/postgres --single \
      -D /var/lib/postgresql/data \
      -c shared_buffers=16GB \
      -c effective_cache_size=64GB \
      -c work_mem=256MB \
      -c max_parallel_workers_per_gather=0 \
      -c jit=off \
      tpch \
  <"${QUERY_FILE}" >"${OUT_DIR}/logs/query.log" 2>"${OUT_DIR}/logs/dynamorio.log"
trace_status=$?
set -e
printf '%s\n' \
  "trace_process_end=$(date --iso-8601=ns)" \
  "trace_process_status=${trace_status}" \
  >>"${OUT_DIR}/alignment-events.txt"
sudo chmod -R a+rwX "${RAW_ROOT}"

mapfile -t active_dr_dirs < <(
  find "${RAW_ROOT}" -type f -name '*.raw.*' -size +1024c -print |
    sed 's|/raw/.*$||' |
    sort -u
)
if [[ "${#active_dr_dirs[@]}" -ne 1 ]]; then
  echo "Expected one substantive PostgreSQL trace root, found ${#active_dr_dirs[@]}" >&2
  exit 1
fi
readonly dr_dir="${active_dr_dirs[0]}"
readonly relative_dr_dir="${dr_dir#${RAW_ROOT}/}"
test -s "${dr_dir}/raw/modules.log"

docker run --rm --user postgres \
  -v "${DYNAMORIO_DIR}:/dynamorio:ro" \
  -v "${SNAPPY_LIB}:/dynamorio-compat/libsnappy.so.1:ro" \
  -v "${RAW_ROOT}:/trace-output" \
  -e LD_LIBRARY_PATH=/dynamorio-compat \
  "${POSTGRES_IMAGE}" \
  "/dynamorio/${DRRAW2TRACE_RELATIVE}" \
    -jobs 20 \
    -indir "/trace-output/${relative_dr_dir}/raw" \
    -chunk_instr_count 10000000 \
  >"${OUT_DIR}/logs/raw2trace.log" 2>&1

mapfile -t trace_zips < <(find "${dr_dir}/trace" -type f -name '*.trace.zip' | sort)
(( ${#trace_zips[@]} >= 1 )) || { echo "No converted trace ZIP found" >&2; exit 1; }
: >"${OUT_DIR}/logs/unzip-test.log"
for trace_zip in "${trace_zips[@]}"; do
  unzip -t "${trace_zip}" >>"${OUT_DIR}/logs/unzip-test.log"
done

docker run --rm --user postgres \
  -v "${DYNAMORIO_DIR}:/dynamorio:ro" \
  -v "${SNAPPY_LIB}:/dynamorio-compat/libsnappy.so.1:ro" \
  -v "${RAW_ROOT}:/trace-output" \
  -e LD_LIBRARY_PATH=/dynamorio-compat \
  "${POSTGRES_IMAGE}" \
  /dynamorio/bin64/drrun \
    -t drcachesim \
    -indir "/trace-output/${relative_dr_dir}/trace" \
    -tool basic_counts \
  >"${OUT_DIR}/logs/basic_counts.log" 2>&1
fetched_instructions="$(awk '/total \(fetched\) instructions/ {print $1; exit}' \
  "${OUT_DIR}/logs/basic_counts.log")"
if [[ ! "${fetched_instructions}" =~ ^[0-9]+$ ]] || \
  (( fetched_instructions < MIN_FETCHED_INSTRUCTIONS )); then
  echo "Converted trace has ${fetched_instructions:-unknown} fetched instructions; minimum is ${MIN_FETCHED_INSTRUCTIONS}" >&2
  exit 1
fi

printf '%s\n' \
  "status=trace_valid" \
  "workload=postgres_tpch_sf${SCALE}_q${QUERY}" \
  "trace_method=single_backend_delayed_instruction_window" \
  "trace_dir=${dr_dir}/trace" \
  "trace_zip_count=${#trace_zips[@]}" \
  "trace_after_instructions=${TRACE_AFTER_INSTRUCTIONS}" \
  "trace_instructions=${TRACE_INSTRUCTIONS}" \
  "minimum_fetched_instructions=${MIN_FETCHED_INSTRUCTIONS}" \
  "fetched_instructions=${fetched_instructions}" \
  "dynamorio_compat_library=${SNAPPY_LIB}" \
  >"${OUT_DIR}/trace-summary.txt"
cat "${OUT_DIR}/trace-summary.txt"
