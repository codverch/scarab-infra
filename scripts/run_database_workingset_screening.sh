#!/usr/bin/env bash

set -euo pipefail

readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly TOOLS_DIR="${DB_ROOT}/tools"
readonly DATA_DIR="${DB_ROOT}/data"
readonly SCREEN_DIR="${DB_ROOT}/working-set-screening"
readonly MYSQL_IMAGE="mysql:8.0.45-debian"
readonly MONGO_IMAGE="mongo:7.0.37-jammy"
readonly BENCHBASE_JRE_IMAGE="eclipse-temurin:21-jre-jammy"
readonly BENCHBASE_DIR="${TOOLS_DIR}/benchbase-mysql"
readonly BENCHBASE_JAR="${BENCHBASE_DIR}/benchbase.jar"
readonly YCSB_DIR="${TOOLS_DIR}/ycsb-mongodb-binding-0.17.0"
readonly SERVER_CPUS="0-23"
readonly CLIENT_CPUS="24-31"
readonly PERF_EVENTS="cycles,instructions,stalled-cycles-backend,cache-misses,branches,branch-misses"
readonly MYSQL_CONTAINER="hpca-mysql-working-set"
readonly MONGO_CONTAINER="hpca-mongo-working-set"
readonly MYSQL_ROOT_PASSWORD="hpca-local-root"
readonly MYSQL_USER="admin"
readonly MYSQL_PASSWORD="password"
readonly MYSQL_PORT=3307
readonly MYSQL_WAREHOUSES=200
readonly MONGO_RECORD_COUNT=10000000
readonly YCSB_THREADS="${YCSB_THREADS:-64}"
readonly MYSQL_WARMUP_SECONDS="${MYSQL_WARMUP_SECONDS:-60}"
readonly MYSQL_MEASURE_SECONDS="${MYSQL_MEASURE_SECONDS:-120}"
readonly MONGO_WARMUP_OPS="${MONGO_WARMUP_OPS:-1000000}"
readonly MONGO_MEASURE_OPS="${MONGO_MEASURE_OPS:-1000000}"

usage() {
  cat <<'EOF'
Usage:
  run_database_workingset_screening.sh mysql <buffer-gb> <memory-gb> <terminals> <repetition>
  run_database_workingset_screening.sh mongodb <cache-gb> <memory-gb> <a|b|c|e|f|scan> <repetition>
  run_database_workingset_screening.sh mongodb-aggregate <cache-gb> <memory-gb> <group|sort> <repetition>

Short pilot defaults can be overridden with MYSQL_WARMUP_SECONDS,
MYSQL_MEASURE_SECONDS, MONGO_WARMUP_OPS, and MONGO_MEASURE_OPS.
EOF
}

require_environment() {
  [[ "$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)" == "/dev/sdb" ]] || {
    echo "${DB_ROOT} is not mounted from /dev/sdb" >&2
    exit 1
  }
  test -f "${BENCHBASE_JAR}"
  test -x "${YCSB_DIR}/bin/ycsb.sh"
  test -f "${DATA_DIR}/.mysql_${MYSQL_WAREHOUSES}w-dataset-ready"
  test -f "${DATA_DIR}/.mongodb_10m-dataset-ready"
  if pgrep -af 'drrun|drraw2trace|/scarab( |$)' | grep -v 'pgrep -af' >/dev/null; then
    echo "A trace or simulation job is active" >&2
    exit 1
  fi
  mkdir -p "${SCREEN_DIR}"
}

stop_databases() {
  docker rm -f hpca-mysql-screen hpca-mongo-screen \
    "${MYSQL_CONTAINER}" "${MONGO_CONTAINER}" >/dev/null 2>&1 || true
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

wait_for_mongodb() {
  for _ in $(seq 1 180); do
    if docker exec "${MONGO_CONTAINER}" mongosh --quiet --eval 'db.adminCommand({ping:1}).ok' \
      2>/dev/null | grep -qx 1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

monitor_command() {
  local run_dir="$1"; shift
  mkdir -p "${run_dir}"
  mpstat -P ALL 1 >"${run_dir}/mpstat.log" &
  local mpstat_pid=$!
  set +e
  sudo perf stat -x, -o "${run_dir}/perf.csv" -C "${SERVER_CPUS}" \
    -e "${PERF_EVENTS}" -- taskset -c "${CLIENT_CPUS}" "$@" \
    >"${run_dir}/workload.log" 2>&1
  local status=$?
  set -e
  kill -INT "${mpstat_pid}" 2>/dev/null || true
  wait "${mpstat_pid}" 2>/dev/null || true
  echo "${status}" >"${run_dir}/exit-status.txt"
  return "${status}"
}

monitor_container_command() {
  local run_dir="$1" container="$2"; shift 2
  local pid cgroup
  pid="$(docker inspect "${container}" --format '{{.State.Pid}}')"
  cgroup="$(cut -d: -f3 "/proc/${pid}/cgroup")"
  [[ -d "/sys/fs/cgroup${cgroup}" ]] || { echo "Missing cgroup ${cgroup}" >&2; return 1; }
  mkdir -p "${run_dir}"
  echo "${cgroup}" >"${run_dir}/server-cgroup.txt"
  mpstat -P ALL 1 >"${run_dir}/mpstat.log" &
  local mpstat_pid=$!
  set +e
  sudo perf stat -x, -o "${run_dir}/perf.csv" -a \
    -e "${PERF_EVENTS}" -G "${cgroup}" -- \
    taskset -c "${CLIENT_CPUS}" "$@" >"${run_dir}/workload.log" 2>&1
  local status=$?
  set -e
  kill -INT "${mpstat_pid}" 2>/dev/null || true
  wait "${mpstat_pid}" 2>/dev/null || true
  echo "${status}" >"${run_dir}/exit-status.txt"
  return "${status}"
}

write_mysql_config() {
  local path="$1" terminals="$2" seconds="$3"
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
  <scalefactor>${MYSQL_WAREHOUSES}</scalefactor>
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
  local config="$1" output="$2"
  printf '%s\0' docker run --rm --network host \
    --cpuset-cpus "${CLIENT_CPUS}" \
    -v "${DB_ROOT}:${DB_ROOT}" \
    -w "${BENCHBASE_DIR}" \
    "${BENCHBASE_JRE_IMAGE}" \
    java -jar "${BENCHBASE_JAR}" -b tpcc -c "${config}" -d "${output}" \
      --create=false --load=false --execute=true
}

run_mysql() {
  local buffer_gb="$1" memory_gb="$2" terminals="$3" repetition="$4"
  local workload="mysql_tpcc_200w_${terminals}t_bp${buffer_gb}g_mem${memory_gb}g_cgroup"
  local run_dir="${SCREEN_DIR}/${workload}/run${repetition}"
  test ! -e "${run_dir}" || { echo "Refusing to overwrite ${run_dir}" >&2; exit 1; }
  (( buffer_gb + 2 <= memory_gb )) || { echo "MySQL memory limit needs at least 2 GiB headroom" >&2; exit 2; }

  stop_databases
  local config="${SCREEN_DIR}/${workload}/mysql.cnf"
  mkdir -p "$(dirname "${config}")"
  cat >"${config}" <<EOF
[mysqld]
innodb_buffer_pool_size=${buffer_gb}G
innodb_buffer_pool_instances=8
skip_log_bin=ON
performance_schema=OFF
max_connections=256
EOF
  docker run --detach --name "${MYSQL_CONTAINER}" \
    --publish "127.0.0.1:${MYSQL_PORT}:3306" \
    --cpuset-cpus "${SERVER_CPUS}" \
    --memory "${memory_gb}g" --memory-swap "${memory_gb}g" \
    -e MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}" \
    -e MYSQL_DATABASE=benchbase -e MYSQL_USER="${MYSQL_USER}" -e MYSQL_PASSWORD="${MYSQL_PASSWORD}" \
    -v "${DATA_DIR}/mysql_200w:/var/lib/mysql" \
    -v "${config}:/etc/mysql/conf.d/hpca.cnf:ro" \
    "${MYSQL_IMAGE}" >/dev/null
  wait_for_mysql || { docker logs "${MYSQL_CONTAINER}"; exit 1; }

  local warmup="${SCREEN_DIR}/${workload}/warmup-run${repetition}.xml"
  local measured="${SCREEN_DIR}/${workload}/measured-run${repetition}.xml"
  write_mysql_config "${warmup}" "${terminals}" "${MYSQL_WARMUP_SECONDS}"
  write_mysql_config "${measured}" "${terminals}" "${MYSQL_MEASURE_SECONDS}"
  local warmup_out="${SCREEN_DIR}/${workload}/warmup-results-run${repetition}"
  mkdir -p "${warmup_out}" "${run_dir}/benchbase-results"
  mapfile -d '' -t warmup_command < <(benchbase_command "${warmup}" "${warmup_out}")
  "${warmup_command[@]}" >"${SCREEN_DIR}/${workload}/warmup-run${repetition}.log" 2>&1
  mapfile -d '' -t measured_command < <(benchbase_command "${measured}" "${run_dir}/benchbase-results")
  monitor_container_command "${run_dir}" "${MYSQL_CONTAINER}" "${measured_command[@]}"
  printf '{"workload":"%s","system":"mysql","repetition":%s,"dataset":"tpcc_200w","terminals":%s,"db_cache_gb":%s,"container_memory_gb":%s}\n' \
    "${workload}" "${repetition}" "${terminals}" "${buffer_gb}" "${memory_gb}" >"${run_dir}/manifest.json"
  docker inspect "${MYSQL_CONTAINER}" --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
    >"${run_dir}/container-memory-bytes.txt"
}

ycsb_command() {
  local profile="$1" operations="$2" insert_start="$3"
  local profile_file="${profile}"
  [[ "${profile}" != "scan" ]] || profile_file=e
  local command=("${YCSB_DIR}/bin/ycsb.sh" run mongodb -s \
    -P "${YCSB_DIR}/workloads/workload${profile_file}" \
    -threads "${YCSB_THREADS}" \
    -p mongodb.url='mongodb://127.0.0.1:27017/ycsb?w=1' \
    -p recordcount="${MONGO_RECORD_COUNT}" -p operationcount="${operations}" \
    -p insertstart="${insert_start}" \
    -p fieldcount=10 -p fieldlength=100)
  if [[ "${profile}" == "scan" ]]; then
    command+=(
      -p readproportion=0 -p updateproportion=0 -p insertproportion=0
      -p scanproportion=1 -p readmodifywriteproportion=0
      -p minscanlength=1 -p maxscanlength=100
    )
  fi
  printf '%s\0' "${command[@]}"
}

run_mongodb() {
  local cache_gb="$1" memory_gb="$2" profile="$3" repetition="$4"
  [[ "${profile}" =~ ^(a|b|c|e|f|scan)$ ]] || { echo "Invalid MongoDB profile" >&2; exit 2; }
  local workload="mongodb_ycsb_${profile}_10m_wt${cache_gb}g_mem${memory_gb}g_cgroup"
  local run_dir="${SCREEN_DIR}/${workload}/run${repetition}"
  test ! -e "${run_dir}" || { echo "Refusing to overwrite ${run_dir}" >&2; exit 1; }
  (( cache_gb + 2 <= memory_gb )) || { echo "MongoDB memory limit needs at least 2 GiB headroom" >&2; exit 2; }
  mkdir -p "${SCREEN_DIR}/${workload}"

  stop_databases
  docker run --detach --name "${MONGO_CONTAINER}" --network host \
    --cpuset-cpus "${SERVER_CPUS}" \
    --memory "${memory_gb}g" --memory-swap "${memory_gb}g" \
    -v "${DATA_DIR}/mongodb_10m:/data/db" \
    "${MONGO_IMAGE}" --wiredTigerCacheSizeGB "${cache_gb}" --bind_ip_all >/dev/null
  wait_for_mongodb || { docker logs "${MONGO_CONTAINER}"; exit 1; }

  # YCSB-E inserts must not collide with the original 10M records, prior
  # experiments, or the measured interval. Other profiles ignore insertstart.
  local key_span=$((MONGO_WARMUP_OPS + MONGO_MEASURE_OPS))
  local warmup_insert_start=0
  local measured_insert_start=0
  if [[ "${profile}" == "e" ]]; then
    warmup_insert_start=$((20000000 + repetition * key_span * 2))
    measured_insert_start=$((warmup_insert_start + MONGO_WARMUP_OPS))
  fi
  mapfile -d '' -t warmup < <(
    ycsb_command "${profile}" "${MONGO_WARMUP_OPS}" "${warmup_insert_start}"
  )
  taskset -c "${CLIENT_CPUS}" "${warmup[@]}" \
    >"${SCREEN_DIR}/${workload}/warmup-run${repetition}.log" 2>&1
  mapfile -d '' -t measured < <(
    ycsb_command "${profile}" "${MONGO_MEASURE_OPS}" "${measured_insert_start}"
  )
  monitor_container_command "${run_dir}" "${MONGO_CONTAINER}" "${measured[@]}"
  printf '{"workload":"%s","system":"mongodb","repetition":%s,"dataset":"ycsb_10m","profile":"%s","db_cache_gb":%s,"container_memory_gb":%s,"warmup_insert_start":%s,"measured_insert_start":%s}\n' \
    "${workload}" "${repetition}" "${profile}" "${cache_gb}" "${memory_gb}" \
    "${warmup_insert_start}" "${measured_insert_start}" >"${run_dir}/manifest.json"
  docker inspect "${MONGO_CONTAINER}" --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
    >"${run_dir}/container-memory-bytes.txt"
  docker exec "${MONGO_CONTAINER}" mongosh --quiet --eval \
    'JSON.stringify(db.serverStatus().wiredTiger.cache)' >"${run_dir}/wiredtiger-cache.json"
}

run_mongodb_aggregate() {
  local cache_gb="$1" memory_gb="$2" query_kind="$3" repetition="$4"
  [[ "${query_kind}" =~ ^(group|sort)$ ]] || { echo "Aggregation must be group or sort" >&2; exit 2; }
  local workload="mongodb_ycsb10m_${query_kind}_aggregate_wt${cache_gb}g_mem${memory_gb}g_cgroup"
  local run_dir="${SCREEN_DIR}/${workload}/run${repetition}"
  test ! -e "${run_dir}" || { echo "Refusing to overwrite ${run_dir}" >&2; exit 1; }
  (( cache_gb + 2 <= memory_gb )) || { echo "MongoDB memory limit needs at least 2 GiB headroom" >&2; exit 2; }
  mkdir -p "${SCREEN_DIR}/${workload}" "${run_dir}"

  stop_databases
  docker run --detach --name "${MONGO_CONTAINER}" --network host \
    --cpuset-cpus "${SERVER_CPUS}" \
    --memory "${memory_gb}g" --memory-swap "${memory_gb}g" \
    -v "${DATA_DIR}/mongodb_10m:/data/db" \
    "${MONGO_IMAGE}" --wiredTigerCacheSizeGB "${cache_gb}" --bind_ip_all >/dev/null
  wait_for_mongodb || { docker logs "${MONGO_CONTAINER}"; exit 1; }

  local javascript
  if [[ "${query_kind}" == "group" ]]; then
    javascript='JSON.stringify(db.getSiblingDB("ycsb").usertable.aggregate([{$project:{prefix:{$substrBytes:["$_id",0,6]},length:{$binarySize:"$field1"}}},{$group:{_id:"$prefix",total_length:{$sum:"$length"},documents:{$sum:1}}},{$sort:{total_length:-1}}],{allowDiskUse:true}).toArray())'
  else
    javascript='db.getSiblingDB("ycsb").usertable.aggregate([{$sort:{field0:1}},{$project:{_id:1}},{$limit:100000}],{allowDiskUse:true}).toArray().length'
  fi
  local client=(docker run --rm --network host --cpuset-cpus "${CLIENT_CPUS}" \
    "${MONGO_IMAGE}" mongosh --quiet mongodb://127.0.0.1:27017 --eval "${javascript}")
  "${client[@]}" >"${SCREEN_DIR}/${workload}/warmup-run${repetition}.log" 2>&1
  monitor_container_command "${run_dir}" "${MONGO_CONTAINER}" \
    /usr/bin/time -f '%e' -o "${run_dir}/duration-seconds.txt" "${client[@]}"
  printf '{"workload":"%s","system":"mongodb_aggregate","repetition":%s,"dataset":"ycsb_10m","query":"%s","db_cache_gb":%s,"container_memory_gb":%s}\n' \
    "${workload}" "${repetition}" "${query_kind}" "${cache_gb}" "${memory_gb}" >"${run_dir}/manifest.json"
  docker inspect "${MONGO_CONTAINER}" --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
    >"${run_dir}/container-memory-bytes.txt"
  docker exec "${MONGO_CONTAINER}" mongosh --quiet --eval \
    'JSON.stringify(db.serverStatus().wiredTiger.cache)' >"${run_dir}/wiredtiger-cache.json"
}

require_environment
case "${1:-}" in
  mysql) [[ $# == 5 ]] || { usage; exit 2; }; run_mysql "$2" "$3" "$4" "$5" ;;
  mongodb) [[ $# == 5 ]] || { usage; exit 2; }; run_mongodb "$2" "$3" "$4" "$5" ;;
  mongodb-aggregate) [[ $# == 5 ]] || { usage; exit 2; }; run_mongodb_aggregate "$2" "$3" "$4" "$5" ;;
  *) usage; exit 2 ;;
esac
