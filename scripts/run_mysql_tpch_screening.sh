#!/usr/bin/env bash

set -euo pipefail

readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly ROOT="${DB_ROOT}/mysql-tpch"
readonly DATA_DIR="${DB_ROOT}/data/mysql_tpch_sf10"
readonly GENERATED_DIR="${DB_ROOT}/postgres-tpch/generated/sf10"
readonly LOAD_DATA_DIR="${ROOT}/load-data"
readonly SOURCE_QUERY_DIR="${DB_ROOT}/postgres-tpch/queries/sf10"
readonly QUERY_NORMALIZER="${DB_ROOT}/control/normalize_mysql_tpch_query.py"
readonly MYSQL_IMAGE="mysql:8.0.45-debian"
readonly MYSQL_CONTAINER="hpca-mysql-tpch"
readonly MYSQL_ROOT_PASSWORD="hpca-local-root"
readonly MYSQL_PORT=3308
readonly SERVER_CPUS="0-23"
readonly CLIENT_CPUS="24-31"
readonly PERF_EVENTS="cycles,instructions,stalled-cycles-backend,cache-misses,branches,branch-misses"
readonly BUFFER_POOL_GB="${BUFFER_POOL_GB:-16}"
readonly CONTAINER_MEMORY_GB="${CONTAINER_MEMORY_GB:-32}"

usage() {
  echo "Usage: run_mysql_tpch_screening.sh prepare | screen <query> <repetition>" >&2
}

require_environment() {
  [[ "$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)" == "/dev/sdb" ]] || {
    echo "${DB_ROOT} is not mounted from /dev/sdb" >&2
    exit 1
  }
  test -s "${GENERATED_DIR}/lineitem.tbl"
  test -s "${SOURCE_QUERY_DIR}/q7.sql"
  test -x "${QUERY_NORMALIZER}"
  mkdir -p "${ROOT}/screening" "${ROOT}/queries" "${DATA_DIR}" "${LOAD_DATA_DIR}"
  sudo chmod 0777 "${DATA_DIR}"
}

stop_databases() {
  docker rm -f hpca-mysql-screen hpca-mongo-screen hpca-mysql-working-set \
    hpca-mongo-working-set "${MYSQL_CONTAINER}" >/dev/null 2>&1 || true
}

start_mysql() {
  stop_databases
  local config="${ROOT}/mysql-tpch.cnf"
  cat >"${config}" <<EOF
[mysqld]
innodb_buffer_pool_size=${BUFFER_POOL_GB}G
innodb_buffer_pool_instances=16
skip_log_bin=ON
performance_schema=OFF
max_connections=128
secure_file_priv=/tpch-data
EOF
  docker run --detach --name "${MYSQL_CONTAINER}" \
    --publish "127.0.0.1:${MYSQL_PORT}:3306" \
    --cpuset-cpus "${SERVER_CPUS}" \
    --memory "${CONTAINER_MEMORY_GB}g" --memory-swap "${CONTAINER_MEMORY_GB}g" \
    -e MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}" \
    -v "${DATA_DIR}:/var/lib/mysql" \
    -v "${LOAD_DATA_DIR}:/tpch-data" \
    -v "${config}:/etc/mysql/conf.d/hpca.cnf:ro" \
    "${MYSQL_IMAGE}" >/dev/null
  for _ in $(seq 1 240); do
    if docker exec "${MYSQL_CONTAINER}" mysqladmin ping \
      --host=127.0.0.1 --user=root --password="${MYSQL_ROOT_PASSWORD}" \
      --silent >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  docker logs "${MYSQL_CONTAINER}" >&2
  return 1
}

mysql_root() {
  docker exec -i "${MYSQL_CONTAINER}" mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" "$@"
}

write_schema() {
  cat >"${ROOT}/schema.sql" <<'SQL'
DROP DATABASE IF EXISTS tpch;
CREATE DATABASE tpch;
USE tpch;
CREATE TABLE region (r_regionkey INT, r_name CHAR(25), r_comment VARCHAR(152)) ENGINE=InnoDB;
CREATE TABLE nation (n_nationkey INT, n_name CHAR(25), n_regionkey INT, n_comment VARCHAR(152)) ENGINE=InnoDB;
CREATE TABLE supplier (s_suppkey INT, s_name CHAR(25), s_address VARCHAR(40), s_nationkey INT, s_phone CHAR(15), s_acctbal DECIMAL(15,2), s_comment VARCHAR(101)) ENGINE=InnoDB;
CREATE TABLE customer (c_custkey INT, c_name VARCHAR(25), c_address VARCHAR(40), c_nationkey INT, c_phone CHAR(15), c_acctbal DECIMAL(15,2), c_mktsegment CHAR(10), c_comment VARCHAR(117)) ENGINE=InnoDB;
CREATE TABLE part (p_partkey INT, p_name VARCHAR(55), p_mfgr CHAR(25), p_brand CHAR(10), p_type VARCHAR(25), p_size INT, p_container CHAR(10), p_retailprice DECIMAL(15,2), p_comment VARCHAR(23)) ENGINE=InnoDB;
CREATE TABLE partsupp (ps_partkey INT, ps_suppkey INT, ps_availqty INT, ps_supplycost DECIMAL(15,2), ps_comment VARCHAR(199)) ENGINE=InnoDB;
CREATE TABLE orders (o_orderkey BIGINT, o_custkey INT, o_orderstatus CHAR(1), o_totalprice DECIMAL(15,2), o_orderdate DATE, o_orderpriority CHAR(15), o_clerk CHAR(15), o_shippriority INT, o_comment VARCHAR(79)) ENGINE=InnoDB;
CREATE TABLE lineitem (l_orderkey BIGINT, l_partkey INT, l_suppkey INT, l_linenumber INT, l_quantity DECIMAL(15,2), l_extendedprice DECIMAL(15,2), l_discount DECIMAL(15,2), l_tax DECIMAL(15,2), l_returnflag CHAR(1), l_linestatus CHAR(1), l_shipdate DATE, l_commitdate DATE, l_receiptdate DATE, l_shipinstruct CHAR(25), l_shipmode CHAR(10), l_comment VARCHAR(44)) ENGINE=InnoDB;
SQL
}

write_load_sql() {
  cat >"${ROOT}/load.sql" <<'SQL'
USE tpch;
SET unique_checks=0;
SET foreign_key_checks=0;
LOAD DATA INFILE '/tpch-data/region.tbl' INTO TABLE region FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (r_regionkey,r_name,r_comment,@discard);
LOAD DATA INFILE '/tpch-data/nation.tbl' INTO TABLE nation FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (n_nationkey,n_name,n_regionkey,n_comment,@discard);
LOAD DATA INFILE '/tpch-data/supplier.tbl' INTO TABLE supplier FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (s_suppkey,s_name,s_address,s_nationkey,s_phone,s_acctbal,s_comment,@discard);
LOAD DATA INFILE '/tpch-data/customer.tbl' INTO TABLE customer FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (c_custkey,c_name,c_address,c_nationkey,c_phone,c_acctbal,c_mktsegment,c_comment,@discard);
LOAD DATA INFILE '/tpch-data/part.tbl' INTO TABLE part FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (p_partkey,p_name,p_mfgr,p_brand,p_type,p_size,p_container,p_retailprice,p_comment,@discard);
LOAD DATA INFILE '/tpch-data/partsupp.tbl' INTO TABLE partsupp FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (ps_partkey,ps_suppkey,ps_availqty,ps_supplycost,ps_comment,@discard);
LOAD DATA INFILE '/tpch-data/orders.tbl' INTO TABLE orders FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (o_orderkey,o_custkey,o_orderstatus,o_totalprice,o_orderdate,o_orderpriority,o_clerk,o_shippriority,o_comment,@discard);
LOAD DATA INFILE '/tpch-data/lineitem.tbl' INTO TABLE lineitem FIELDS TERMINATED BY '|' LINES TERMINATED BY '\n' (l_orderkey,l_partkey,l_suppkey,l_linenumber,l_quantity,l_extendedprice,l_discount,l_tax,l_returnflag,l_linestatus,l_shipdate,l_commitdate,l_receiptdate,l_shipinstruct,l_shipmode,l_comment,@discard);
SQL
}

write_indexes() {
  cat >"${ROOT}/indexes.sql" <<'SQL'
USE tpch;
ALTER TABLE nation ADD PRIMARY KEY (n_nationkey);
ALTER TABLE region ADD PRIMARY KEY (r_regionkey);
ALTER TABLE supplier ADD PRIMARY KEY (s_suppkey), ADD INDEX supplier_nation_idx (s_nationkey);
ALTER TABLE customer ADD PRIMARY KEY (c_custkey), ADD INDEX customer_nation_idx (c_nationkey);
ALTER TABLE part ADD PRIMARY KEY (p_partkey);
ALTER TABLE partsupp ADD PRIMARY KEY (ps_partkey,ps_suppkey);
ALTER TABLE orders ADD PRIMARY KEY (o_orderkey), ADD INDEX orders_customer_idx (o_custkey);
ALTER TABLE lineitem ADD PRIMARY KEY (l_orderkey,l_linenumber), ADD INDEX lineitem_q7_idx (l_shipdate,l_suppkey,l_orderkey);
ANALYZE TABLE nation,supplier,customer,orders,lineitem;
SQL
}

write_queries() {
  local query
  for query in $(seq 1 22); do
    "${QUERY_NORMALIZER}" <"${SOURCE_QUERY_DIR}/q${query}.sql" \
      >"${ROOT}/queries/q${query}.sql"
  done
}

ensure_primary_key() {
  local table="$1" columns="$2"
  local count
  count="$(mysql_root -N -e "SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema='tpch' AND table_name='${table}' AND constraint_type='PRIMARY KEY'")"
  if [[ "${count}" == "0" ]]; then
    echo "Adding missing tpch.${table} primary key (${columns})"
    mysql_root tpch -e "ALTER TABLE ${table} ADD PRIMARY KEY (${columns})"
  fi
}

ensure_full_primary_keys() {
  ensure_primary_key region r_regionkey
  ensure_primary_key nation n_nationkey
  ensure_primary_key supplier s_suppkey
  ensure_primary_key customer c_custkey
  ensure_primary_key part p_partkey
  ensure_primary_key partsupp 'ps_partkey,ps_suppkey'
  ensure_primary_key orders o_orderkey
  ensure_primary_key lineitem 'l_orderkey,l_linenumber'
}

prepare() {
  local marker="${ROOT}/.sf10-ready"
  if [[ ! -s "${LOAD_DATA_DIR}/lineitem.tbl" ]]; then
    cp --reflink=auto "${GENERATED_DIR}"/*.tbl "${LOAD_DATA_DIR}/"
  fi
  start_mysql
  write_queries
  if [[ -f "${marker}" ]]; then
    ensure_full_primary_keys
    echo "MySQL TPC-H SF10 already prepared"
    return 0
  fi
  write_schema
  write_load_sql
  write_indexes
  mysql_root <"${ROOT}/schema.sql"
  mysql_root <"${ROOT}/load.sql" >"${ROOT}/load.log" 2>&1
  mysql_root <"${ROOT}/indexes.sql" >"${ROOT}/indexes.log" 2>&1
  mysql_root -N -e 'SELECT "region",COUNT(*) FROM tpch.region UNION ALL SELECT "nation",COUNT(*) FROM tpch.nation UNION ALL SELECT "supplier",COUNT(*) FROM tpch.supplier UNION ALL SELECT "customer",COUNT(*) FROM tpch.customer UNION ALL SELECT "orders",COUNT(*) FROM tpch.orders UNION ALL SELECT "lineitem",COUNT(*) FROM tpch.lineitem' \
    >"${ROOT}/row-counts.txt"
  touch "${marker}"
}

client_command() {
  local query="$1"
  printf '%s\0' docker run --rm --network host --cpuset-cpus "${CLIENT_CPUS}" \
    -v "${ROOT}/queries/q${query}.sql:/query.sql:ro" "${MYSQL_IMAGE}" \
    sh -lc "mysql -h127.0.0.1 -P${MYSQL_PORT} -uroot -p${MYSQL_ROOT_PASSWORD} --batch --skip-column-names tpch < /query.sql"
}

screen() {
  local query="$1" repetition="$2"
  [[ "${query}" =~ ^([1-9]|1[0-9]|2[0-2])$ ]] || { echo "Query must be 1-22" >&2; exit 2; }
  local workload="mysql_tpch_sf10_q${query}_bp${BUFFER_POOL_GB}g_mem${CONTAINER_MEMORY_GB}g_cgroup"
  local run_dir="${ROOT}/screening/${workload}/run${repetition}"
  test ! -e "${run_dir}" || { echo "Refusing to overwrite ${run_dir}" >&2; exit 1; }
  mkdir -p "${ROOT}/screening/${workload}"
  prepare
  mapfile -d '' -t command < <(client_command "${query}")
  "${command[@]}" >"${ROOT}/screening/${workload}/warmup-run${repetition}.log" 2>&1
  mkdir -p "${run_dir}"
  local pid cgroup mpstat_pid status
  pid="$(docker inspect "${MYSQL_CONTAINER}" --format '{{.State.Pid}}')"
  cgroup="$(cut -d: -f3 "/proc/${pid}/cgroup")"
  echo "${cgroup}" >"${run_dir}/server-cgroup.txt"
  mpstat -P ALL 1 >"${run_dir}/mpstat.log" &
  mpstat_pid=$!
  set +e
  sudo perf stat -x, -o "${run_dir}/perf.csv" -a -e "${PERF_EVENTS}" -G "${cgroup}" -- \
    /usr/bin/time -f '%e' -o "${run_dir}/duration-seconds.txt" "${command[@]}" \
    >"${run_dir}/workload.log" 2>&1
  status=$?
  set -e
  kill -INT "${mpstat_pid}" 2>/dev/null || true
  wait "${mpstat_pid}" 2>/dev/null || true
  echo "${status}" >"${run_dir}/exit-status.txt"
  printf '{"workload":"%s","system":"mysql_tpch","repetition":%s,"dataset":"tpch_sf10","query":%s,"db_cache_gb":%s,"container_memory_gb":%s}\n' \
    "${workload}" "${repetition}" "${query}" "${BUFFER_POOL_GB}" "${CONTAINER_MEMORY_GB}" >"${run_dir}/manifest.json"
  (( status == 0 ))
}

require_environment
case "${1:-}" in
  prepare) [[ $# == 1 ]] || { usage; exit 2; }; prepare ;;
  screen) [[ $# == 3 ]] || { usage; exit 2; }; screen "$2" "$3" ;;
  *) usage; exit 2 ;;
esac
