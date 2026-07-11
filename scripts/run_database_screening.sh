#!/usr/bin/env bash

set -euo pipefail

readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly TOOLS_DIR="${DB_ROOT}/tools"
readonly DATA_DIR="${DB_ROOT}/data"
readonly SCREEN_DIR="${DB_ROOT}/screening"
readonly MYSQL_IMAGE="mysql:8.0.45-debian"
readonly MONGO_IMAGE="mongo:7.0.37-jammy"
readonly BENCHBASE_JRE_IMAGE="eclipse-temurin:21-jre-jammy"
readonly BENCHBASE_DIR="${TOOLS_DIR}/benchbase-mysql"
readonly BENCHBASE_JAR="${BENCHBASE_DIR}/benchbase.jar"
readonly YCSB_DIR="${TOOLS_DIR}/ycsb-mongodb-binding-0.17.0"
readonly SERVER_CPUS="0-23"
readonly CLIENT_CPUS="24-31"
readonly PERF_EVENTS="cycles,instructions,stalled-cycles-backend,cache-misses,branches,branch-misses"
readonly MYSQL_ROOT_PASSWORD="hpca-local-root"
readonly MYSQL_USER="admin"
readonly MYSQL_PASSWORD="password"
readonly MYSQL_CONTAINER="hpca-mysql-screen"
readonly MONGO_CONTAINER="hpca-mongo-screen"
readonly MYSQL_PORT="3307"
readonly MYSQL_WARMUP_SECONDS="${MYSQL_WARMUP_SECONDS:-300}"
readonly MYSQL_MEASURE_SECONDS="${MYSQL_MEASURE_SECONDS:-600}"
readonly MONGO_RECORD_COUNT="${MONGO_RECORD_COUNT:-10000000}"
readonly MONGO_WARMUP_OPS="${MONGO_WARMUP_OPS:-5000000}"
readonly MONGO_MEASURE_OPS="${MONGO_MEASURE_OPS:-5000000}"
readonly YCSB_THREADS="${YCSB_THREADS:-64}"

mongo_dataset_suffix() {
  if [[ "${MONGO_RECORD_COUNT}" == "10000000" ]]; then
    printf '10m'
  else
    printf '%sr' "${MONGO_RECORD_COUNT}"
  fi
}

usage() {
  cat <<'EOF'
Usage:
  run_database_screening.sh prepare-mysql <warehouses>
  run_database_screening.sh screen-mysql <warehouses> <terminals> <repetition>
  run_database_screening.sh prepare-mongodb
  run_database_screening.sh screen-mongodb <a|c|e|f> <repetition>
  run_database_screening.sh run-all
EOF
}

require_environment() {
  [[ "$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)" == "/dev/sdb" ]] || {
    echo "${DB_ROOT} is not mounted from /dev/sdb" >&2
    exit 1
  }
  [[ -f "${BENCHBASE_JAR}" ]] || { echo "Run bootstrap_database_screening.sh first" >&2; exit 1; }
  [[ -x "${YCSB_DIR}/bin/ycsb.sh" ]] || { echo "YCSB is missing" >&2; exit 1; }
  if pgrep -af 'drrun|drraw2trace' | grep -v 'pgrep -af' >/dev/null; then
    echo "A DynamoRIO trace job is active; refusing to interfere" >&2
    exit 1
  fi
}

stop_container() {
  docker rm -f "$1" >/dev/null 2>&1 || true
}

wait_for_port() {
  local port="$1"
  for _ in $(seq 1 180); do
    nc -z 127.0.0.1 "${port}" && return 0
    sleep 1
  done
  return 1
}

wait_for_mysql() {
  for _ in $(seq 1 180); do
    if docker exec "${MYSQL_CONTAINER}" mysqladmin ping \
      --host=127.0.0.1 --user=root --password="${MYSQL_ROOT_PASSWORD}" \
      --silent >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

write_mysql_config() {
  local path="$1" warehouses="$2" terminals="$3" seconds="$4"
  cat >"${path}" <<EOF
<?xml version="1.0"?>
<parameters>
  <type>MYSQL</type>
  <driver>com.mysql.cj.jdbc.Driver</driver>
  <url>jdbc:mysql://localhost:${MYSQL_PORT}/benchbase?rewriteBatchedStatements=true&amp;allowPublicKeyRetrieval=True&amp;sslMode=DISABLED</url>
  <username>${MYSQL_USER}</username>
  <password>${MYSQL_PASSWORD}</password>
  <isolation>TRANSACTION_SERIALIZABLE</isolation>
  <batchsize>128</batchsize>
  <scalefactor>${warehouses}</scalefactor>
  <terminals>${terminals}</terminals>
  <works><work><time>${seconds}</time><rate>unlimited</rate><weights>45,43,4,4,4</weights></work></works>
  <transactiontypes>
    <transactiontype><name>NewOrder</name></transactiontype>
    <transactiontype><name>Payment</name></transactiontype>
    <transactiontype><name>OrderStatus</name></transactiontype>
    <transactiontype><name>Delivery</name></transactiontype>
    <transactiontype><name>StockLevel</name></transactiontype>
  </transactiontypes>
</parameters>
EOF
}

benchbase_command() {
  local config="$1" output="$2" create="$3" load="$4" execute="$5"
  printf '%s\0' docker run --rm --network host \
    --cpuset-cpus "${CLIENT_CPUS}" \
    -v "${DB_ROOT}:${DB_ROOT}" \
    -w "${BENCHBASE_DIR}" \
    "${BENCHBASE_JRE_IMAGE}" \
    java -jar "${BENCHBASE_JAR}" -b tpcc -c "${config}" -d "${output}" \
    --create="${create}" --load="${load}" --execute="${execute}"
}

start_mysql() {
  local warehouses="$1"
  local data="${DATA_DIR}/mysql_${warehouses}w"
  local config="${DB_ROOT}/mysql-screen.cnf"
  mkdir -p "${data}"
  sudo chmod 0777 "${data}"
  cat >"${config}" <<'EOF'
[mysqld]
innodb_buffer_pool_size=32G
innodb_buffer_pool_instances=16
skip_log_bin=ON
performance_schema=OFF
max_connections=256
EOF
  stop_container "${MYSQL_CONTAINER}"
  docker run --detach --name "${MYSQL_CONTAINER}" \
    --publish "127.0.0.1:${MYSQL_PORT}:3306" \
    --cpuset-cpus "${SERVER_CPUS}" \
    -e MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}" \
    -e MYSQL_DATABASE=benchbase \
    -e MYSQL_USER="${MYSQL_USER}" \
    -e MYSQL_PASSWORD="${MYSQL_PASSWORD}" \
    -v "${data}:/var/lib/mysql" \
    -v "${config}:/etc/mysql/conf.d/hpca.cnf:ro" \
    "${MYSQL_IMAGE}" >/dev/null
  wait_for_mysql || { docker logs "${MYSQL_CONTAINER}"; exit 1; }
}

prepare_mysql() {
  local warehouses="$1"
  local marker="${DATA_DIR}/.mysql_${warehouses}w-dataset-ready"
  start_mysql "${warehouses}"
  [[ -f "${marker}" ]] && { echo "MySQL ${warehouses}w dataset already prepared"; return; }
  config="${SCREEN_DIR}/mysql_${warehouses}w_load.xml"
  mkdir -p "${SCREEN_DIR}"
  write_mysql_config "${config}" "${warehouses}" 64 1
  load_output="${SCREEN_DIR}/mysql_${warehouses}w_load_results"
  mkdir -p "${load_output}"
  mapfile -d '' -t command < <(benchbase_command "${config}" "${load_output}" true true false)
  "${command[@]}" 2>&1 | tee "${SCREEN_DIR}/mysql_${warehouses}w_load.log"
  touch "${marker}"
}

monitor_command() {
  local run_dir="$1"; shift
  mkdir -p "${run_dir}"
  mpstat -P ALL 1 >"${run_dir}/mpstat.log" &
  mpstat_pid=$!
  set +e
  sudo perf stat -x, -o "${run_dir}/perf.csv" -C "${SERVER_CPUS}" \
    -e "${PERF_EVENTS}" -- taskset -c "${CLIENT_CPUS}" "$@" \
    >"${run_dir}/workload.log" 2>&1
  status=$?
  set -e
  kill -INT "${mpstat_pid}" 2>/dev/null || true
  wait "${mpstat_pid}" 2>/dev/null || true
  echo "${status}" >"${run_dir}/exit-status.txt"
  (( status == 0 )) || return "${status}"
}

screen_mysql() {
  local warehouses="$1" terminals="$2" repetition="$3"
  local workload="mysql_tpcc_${warehouses}w_${terminals}t"
  local run_dir="${SCREEN_DIR}/${workload}/run${repetition}"
  [[ ! -e "${run_dir}" ]] || { echo "Refusing to overwrite ${run_dir}" >&2; exit 1; }
  start_mysql "${warehouses}"
  [[ -f "${DATA_DIR}/.mysql_${warehouses}w-dataset-ready" ]] || {
    echo "Prepare the MySQL dataset first" >&2; exit 1;
  }
  warmup="${SCREEN_DIR}/${workload}/warmup-run${repetition}.xml"
  measured="${SCREEN_DIR}/${workload}/measured-run${repetition}.xml"
  mkdir -p "$(dirname "${warmup}")"
  write_mysql_config "${warmup}" "${warehouses}" "${terminals}" "${MYSQL_WARMUP_SECONDS}"
  write_mysql_config "${measured}" "${warehouses}" "${terminals}" "${MYSQL_MEASURE_SECONDS}"
  warmup_output="${SCREEN_DIR}/${workload}/warmup-results-run${repetition}"
  mkdir -p "${warmup_output}"
  mapfile -d '' -t warmup_command < <(
    benchbase_command "${warmup}" "${warmup_output}" false false true
  )
  "${warmup_command[@]}" >"${SCREEN_DIR}/${workload}/warmup-run${repetition}.log" 2>&1
  mkdir -p "${run_dir}/benchbase-results"
  mapfile -d '' -t measured_command < <(
    benchbase_command "${measured}" "${run_dir}/benchbase-results" false false true
  )
  monitor_command "${run_dir}" "${measured_command[@]}"
  printf '{"workload":"%s","system":"mysql","repetition":%s}\n' \
    "${workload}" "${repetition}" >"${run_dir}/manifest.json"
}

start_mongodb() {
  local data="${DATA_DIR}/mongodb_$(mongo_dataset_suffix)"
  mkdir -p "${data}"
  sudo chmod 0777 "${data}"
  stop_container "${MONGO_CONTAINER}"
  docker run --detach --name "${MONGO_CONTAINER}" --network host \
    --cpuset-cpus "${SERVER_CPUS}" \
    -v "${data}:/data/db" \
    "${MONGO_IMAGE}" --wiredTigerCacheSizeGB 32 --bind_ip_all >/dev/null
  wait_for_port 27017 || { docker logs "${MONGO_CONTAINER}"; exit 1; }
}

ycsb_command() {
  local mode="$1" profile="$2" operations="$3"
  printf '%s\0' "${YCSB_DIR}/bin/ycsb.sh" "${mode}" mongodb -s \
    -P "${YCSB_DIR}/workloads/workload${profile}" \
    -threads "${YCSB_THREADS}" \
    -p mongodb.url='mongodb://127.0.0.1:27017/ycsb?w=1' \
    -p recordcount="${MONGO_RECORD_COUNT}" \
    -p operationcount="${operations}" \
    -p fieldcount=10 \
    -p fieldlength=100
}

prepare_mongodb() {
  local suffix marker
  suffix="$(mongo_dataset_suffix)"
  marker="${DATA_DIR}/.mongodb_${suffix}-dataset-ready"
  start_mongodb
  [[ -f "${marker}" ]] && { echo "MongoDB ${suffix} dataset already prepared"; return; }
  mapfile -d '' -t command < <(ycsb_command load a "${MONGO_RECORD_COUNT}")
  taskset -c "${CLIENT_CPUS}" "${command[@]}" 2>&1 | tee "${SCREEN_DIR}/mongodb_${suffix}_load.log"
  touch "${marker}"
}

screen_mongodb() {
  local profile="$1" repetition="$2"
  [[ "${profile}" =~ ^[acef]$ ]] || { echo "Profile must be a, c, e, or f" >&2; exit 1; }
  local suffix workload
  suffix="$(mongo_dataset_suffix)"
  workload="mongodb_ycsb_${profile}_${suffix}"
  local run_dir="${SCREEN_DIR}/${workload}/run${repetition}"
  [[ ! -e "${run_dir}" ]] || { echo "Refusing to overwrite ${run_dir}" >&2; exit 1; }
  start_mongodb
  [[ -f "${DATA_DIR}/.mongodb_${suffix}-dataset-ready" ]] || {
    echo "Prepare MongoDB first" >&2; exit 1;
  }
  mapfile -d '' -t warmup < <(ycsb_command run "${profile}" "${MONGO_WARMUP_OPS}")
  taskset -c "${CLIENT_CPUS}" "${warmup[@]}" \
    >"${SCREEN_DIR}/${workload}-warmup-run${repetition}.log" 2>&1
  mapfile -d '' -t measured < <(ycsb_command run "${profile}" "${MONGO_MEASURE_OPS}")
  monitor_command "${run_dir}" "${measured[@]}"
  printf '{"workload":"%s","system":"mongodb","repetition":%s}\n' \
    "${workload}" "${repetition}" >"${run_dir}/manifest.json"
}

run_all() {
  prepare_mysql 100
  for repetition in 1 2; do screen_mysql 100 32 "${repetition}"; done
  for repetition in 1 2; do screen_mysql 100 64 "${repetition}"; done
  prepare_mysql 200
  for repetition in 1 2; do screen_mysql 200 64 "${repetition}"; done
  prepare_mongodb
  for profile in a c e f; do
    for repetition in 1 2; do screen_mongodb "${profile}" "${repetition}"; done
  done
}

require_environment
case "${1:-}" in
  prepare-mysql) [[ $# == 2 ]] || { usage; exit 2; }; prepare_mysql "$2" ;;
  screen-mysql) [[ $# == 4 ]] || { usage; exit 2; }; screen_mysql "$2" "$3" "$4" ;;
  prepare-mongodb) [[ $# == 1 ]] || { usage; exit 2; }; prepare_mongodb ;;
  screen-mongodb) [[ $# == 3 ]] || { usage; exit 2; }; screen_mongodb "$2" "$3" ;;
  run-all) [[ $# == 1 ]] || { usage; exit 2; }; run_all ;;
  *) usage; exit 2 ;;
esac
