#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly ROOT="${DB_ROOT}/postgres-tpch"
readonly TPCH_DIR="${ROOT}/tools/tpch-kit/dbgen"
readonly DATA_DIR="${ROOT}/data"
readonly GENERATED_DIR="${ROOT}/generated"
readonly QUERY_DIR="${ROOT}/queries"
readonly SCREEN_DIR="${ROOT}/screening"
readonly POSTGRES_IMAGE="postgres:16.10-bookworm"
readonly POSTGRES_CONTAINER="hpca-postgres-tpch"
readonly POSTGRES_PASSWORD="hpca-local-postgres"
readonly POSTGRES_PORT="5434"
readonly SERVER_CPUS="0-23"
readonly CLIENT_CPUS="24-31"
readonly PERF_EVENTS="cycles,instructions,stalled-cycles-backend,cache-misses,branches,branch-misses"
readonly QUERY_NORMALIZER="${SCRIPT_DIR}/normalize_postgres_tpch_query.py"

usage() {
  cat <<'EOF'
Usage:
  run_postgres_tpch_screening.sh prepare <scale-factor>
  run_postgres_tpch_screening.sh screen-all <scale-factor>
  run_postgres_tpch_screening.sh screen-query <scale-factor> <query> <repetition>
EOF
}

require_environment() {
  [[ "$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)" == "/dev/sdb" ]] || {
    echo "${DB_ROOT} is not mounted from /dev/sdb" >&2
    exit 1
  }
  test -x "${TPCH_DIR}/dbgen" || { echo "Run bootstrap_postgres_tpch.sh first" >&2; exit 1; }
  test -x "${TPCH_DIR}/qgen" || { echo "Run bootstrap_postgres_tpch.sh first" >&2; exit 1; }
  test -x "${QUERY_NORMALIZER}" || { echo "Missing ${QUERY_NORMALIZER}" >&2; exit 1; }
  if pgrep -af 'drrun|drraw2trace' | grep -v 'pgrep -af' >/dev/null; then
    echo "A DynamoRIO job is active; refusing to interfere" >&2
    exit 1
  fi
}

stop_postgres() {
  docker rm -f "${POSTGRES_CONTAINER}" >/dev/null 2>&1 || true
}

start_postgres() {
  local scale="$1"
  local data="${DATA_DIR}/postgres_sf${scale}"
  mkdir -p "${data}"
  sudo chmod 0777 "${data}"
  stop_postgres
  docker run --detach --name "${POSTGRES_CONTAINER}" \
    --cpuset-cpus "${SERVER_CPUS}" \
    --publish "127.0.0.1:${POSTGRES_PORT}:5432" \
    -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
    -e POSTGRES_DB=tpch \
    -v "${data}:/var/lib/postgresql/data" \
    "${POSTGRES_IMAGE}" \
    -c shared_buffers=16GB \
    -c effective_cache_size=64GB \
    -c work_mem=256MB \
    -c maintenance_work_mem=2GB \
    -c max_parallel_workers_per_gather=0 \
    -c jit=off >/dev/null

  for _ in $(seq 1 180); do
    if docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" "${POSTGRES_CONTAINER}" \
      psql -U postgres -d tpch -Atc 'SELECT 1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  docker logs "${POSTGRES_CONTAINER}" >&2
  return 1
}

write_schema() {
  local path="$1"
  cat >"${path}" <<'SQL'
CREATE TABLE region (r_regionkey integer, r_name char(25), r_comment varchar(152));
CREATE TABLE nation (n_nationkey integer, n_name char(25), n_regionkey integer, n_comment varchar(152));
CREATE TABLE supplier (s_suppkey integer, s_name char(25), s_address varchar(40), s_nationkey integer, s_phone char(15), s_acctbal numeric(15,2), s_comment varchar(101));
CREATE TABLE customer (c_custkey integer, c_name varchar(25), c_address varchar(40), c_nationkey integer, c_phone char(15), c_acctbal numeric(15,2), c_mktsegment char(10), c_comment varchar(117));
CREATE TABLE part (p_partkey integer, p_name varchar(55), p_mfgr char(25), p_brand char(10), p_type varchar(25), p_size integer, p_container char(10), p_retailprice numeric(15,2), p_comment varchar(23));
CREATE TABLE partsupp (ps_partkey integer, ps_suppkey integer, ps_availqty integer, ps_supplycost numeric(15,2), ps_comment varchar(199));
CREATE TABLE orders (o_orderkey bigint, o_custkey integer, o_orderstatus char(1), o_totalprice numeric(15,2), o_orderdate date, o_orderpriority char(15), o_clerk char(15), o_shippriority integer, o_comment varchar(79));
CREATE TABLE lineitem (l_orderkey bigint, l_partkey integer, l_suppkey integer, l_linenumber integer, l_quantity numeric(15,2), l_extendedprice numeric(15,2), l_discount numeric(15,2), l_tax numeric(15,2), l_returnflag char(1), l_linestatus char(1), l_shipdate date, l_commitdate date, l_receiptdate date, l_shipinstruct char(25), l_shipmode char(10), l_comment varchar(44));
SQL
}

write_indexes() {
  local path="$1"
  cat >"${path}" <<'SQL'
ALTER TABLE region ADD PRIMARY KEY (r_regionkey);
ALTER TABLE nation ADD PRIMARY KEY (n_nationkey);
ALTER TABLE supplier ADD PRIMARY KEY (s_suppkey);
ALTER TABLE customer ADD PRIMARY KEY (c_custkey);
ALTER TABLE part ADD PRIMARY KEY (p_partkey);
ALTER TABLE partsupp ADD PRIMARY KEY (ps_partkey, ps_suppkey);
ALTER TABLE orders ADD PRIMARY KEY (o_orderkey);
ALTER TABLE lineitem ADD PRIMARY KEY (l_orderkey, l_linenumber);
CREATE INDEX customer_nation_idx ON customer (c_nationkey);
CREATE INDEX supplier_nation_idx ON supplier (s_nationkey);
CREATE INDEX partsupp_supplier_idx ON partsupp (ps_suppkey);
CREATE INDEX orders_customer_idx ON orders (o_custkey);
CREATE INDEX orders_date_idx ON orders (o_orderdate);
CREATE INDEX lineitem_part_supplier_idx ON lineitem (l_partkey, l_suppkey);
CREATE INDEX lineitem_shipdate_idx ON lineitem (l_shipdate);
ANALYZE;
SQL
}

psql_server() {
  docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${POSTGRES_CONTAINER}" \
    psql -v ON_ERROR_STOP=1 -U postgres -d tpch "$@"
}

load_table() {
  local table="$1" file="$2"
  sed 's/|$//' "${file}" | docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" \
    "${POSTGRES_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d tpch \
    -c "COPY ${table} FROM STDIN WITH (FORMAT csv, DELIMITER '|');"
}

generate_queries() {
  local scale="$1"
  local destination="${QUERY_DIR}/sf${scale}"
  mkdir -p "${destination}"
  [[ ! -f "${destination}/.normalized-v1" ]] || return 0
  for query in $(seq 1 22); do
    raw="${destination}/q${query}.raw.sql"
    (cd "${TPCH_DIR}" && DSS_QUERY="${TPCH_DIR}/queries" ./qgen -s "${scale}" "${query}") \
      >"${raw}"
    "${QUERY_NORMALIZER}" <"${raw}" >"${destination}/q${query}.sql"
  done
  touch "${destination}/.normalized-v1"
}

prepare() {
  local scale="$1"
  local marker="${DATA_DIR}/.postgres_sf${scale}-ready"
  local generated="${GENERATED_DIR}/sf${scale}"
  mkdir -p "${generated}" "${SCREEN_DIR}/sf${scale}"
  if [[ ! -f "${generated}/lineitem.tbl" ]]; then
    (cd "${TPCH_DIR}" && DSS_PATH="${generated}" DSS_CONFIG="${TPCH_DIR}" \
      ./dbgen -vf -s "${scale}") \
      >"${SCREEN_DIR}/sf${scale}/dbgen.log" 2>&1
  fi
  generate_queries "${scale}"
  start_postgres "${scale}"
  [[ ! -f "${marker}" ]] || { echo "PostgreSQL TPC-H SF${scale} already prepared"; return; }

  schema="${SCREEN_DIR}/sf${scale}/schema.sql"
  indexes="${SCREEN_DIR}/sf${scale}/indexes.sql"
  write_schema "${schema}"
  write_indexes "${indexes}"
  psql_server -f - <"${schema}"
  for table in region nation supplier customer part partsupp orders lineitem; do
    load_table "${table}" "${generated}/${table}.tbl"
  done
  psql_server -f - <"${indexes}"
  touch "${marker}"
}

client_command() {
  local query_file="$1"
  printf '%s\0' docker run --rm --network host \
    --cpuset-cpus "${CLIENT_CPUS}" \
    -e PGPASSWORD="${POSTGRES_PASSWORD}" \
    -v "${query_file}:/query.sql:ro" \
    "${POSTGRES_IMAGE}" \
    psql -X -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "${POSTGRES_PORT}" \
      -U postgres -d tpch -o /dev/null -f /query.sql
}

run_measured() {
  local scale="$1" query="$2" repetition="$3"
  local workload="postgres_tpch_sf${scale}_q${query}"
  local run_dir="${SCREEN_DIR}/${workload}/run${repetition}"
  if [[ -f "${run_dir}/exit-status.txt" ]] && [[ "$(<"${run_dir}/exit-status.txt")" == "0" ]]; then
    echo "Skipping completed ${run_dir}"
    return
  fi
  [[ ! -e "${run_dir}" ]] || {
    echo "Refusing partial or failed run directory: ${run_dir}" >&2
    return 1
  }
  mkdir -p "${run_dir}"
  mapfile -d '' -t command < <(client_command "${QUERY_DIR}/sf${scale}/q${query}.sql")
  mpstat -P ALL 1 >"${run_dir}/mpstat.log" &
  mpstat_pid=$!
  set +e
  sudo perf stat -x, -o "${run_dir}/perf.csv" -C "${SERVER_CPUS}" \
    -e "${PERF_EVENTS}" -- \
    /usr/bin/time -f '%e' -o "${run_dir}/duration-seconds.txt" "${command[@]}" \
    >"${run_dir}/workload.log" 2>&1
  status=$?
  set -e
  kill -INT "${mpstat_pid}" 2>/dev/null || true
  wait "${mpstat_pid}" 2>/dev/null || true
  echo "${status}" >"${run_dir}/exit-status.txt"
  printf '{"workload":"%s","system":"postgresql","scale_factor":%s,"query":%s,"repetition":%s}\n' \
    "${workload}" "${scale}" "${query}" "${repetition}" >"${run_dir}/manifest.json"
  (( status == 0 )) || return "${status}"
}

warmup_query() {
  local scale="$1" query="$2"
  local log="${SCREEN_DIR}/postgres_tpch_sf${scale}_q${query}/warmup.log"
  local marker="${SCREEN_DIR}/postgres_tpch_sf${scale}_q${query}/warmup.ok"
  [[ ! -f "${marker}" ]] || return 0
  mkdir -p "$(dirname "${log}")"
  if [[ -e "${log}" ]]; then
    mv "${log}" "${log}.failed.$(date +%Y%m%d%H%M%S)"
  fi
  mapfile -d '' -t command < <(client_command "${QUERY_DIR}/sf${scale}/q${query}.sql")
  "${command[@]}" >"${log}" 2>&1
  touch "${marker}"
}

screen_all() {
  local scale="$1"
  prepare "${scale}"
  start_postgres "${scale}"
  for query in $(seq 1 22); do warmup_query "${scale}" "${query}"; done
  for repetition in 1 2; do
    for query in $(seq 1 22); do run_measured "${scale}" "${query}" "${repetition}"; done
  done
}

screen_query() {
  local scale="$1" query="$2" repetition="$3"
  prepare "${scale}"
  start_postgres "${scale}"
  warmup_query "${scale}" "${query}"
  run_measured "${scale}" "${query}" "${repetition}"
}

require_environment
case "${1:-}" in
  prepare) [[ $# == 2 ]] || { usage; exit 2; }; prepare "$2" ;;
  screen-all) [[ $# == 2 ]] || { usage; exit 2; }; screen_all "$2" ;;
  screen-query) [[ $# == 4 ]] || { usage; exit 2; }; screen_query "$2" "$3" "$4" ;;
  *) usage; exit 2 ;;
esac
