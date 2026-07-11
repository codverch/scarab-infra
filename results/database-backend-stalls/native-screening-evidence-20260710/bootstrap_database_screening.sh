#!/usr/bin/env bash

set -euo pipefail

readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly TOOLS_DIR="${DB_ROOT}/tools"
readonly MANIFEST_DIR="${DB_ROOT}/manifests"
readonly MYSQL_IMAGE="mysql:8.0.45-debian"
readonly MONGO_IMAGE="mongo:7.0.37-jammy"
readonly BENCHBASE_BUILD_IMAGE="maven:3.9.9-eclipse-temurin-21"
readonly BENCHBASE_JRE_IMAGE="eclipse-temurin:21-jre-jammy"
readonly BENCHBASE_REPO="https://github.com/cmu-db/benchbase.git"
readonly BENCHBASE_TAG="v2023"
readonly BENCHBASE_COMMIT="b4b36683afdf79dd8bf70b95199b874c68218975"
readonly YCSB_URL="https://github.com/brianfrankcooper/YCSB/releases/download/0.17.0/ycsb-mongodb-binding-0.17.0.tar.gz"
readonly YCSB_SHA256="6a054a706812269c80bfc6ed1e83457990c1c60b01f5083c873aaed05577e30d"

source_device="$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)"
if [[ "${source_device}" != "/dev/sdb" ]]; then
  echo "${DB_ROOT} must be mounted directly from /dev/sdb; found ${source_device:-nothing}" >&2
  exit 1
fi
available_bytes="$(findmnt -n -b -o AVAIL --target "${DB_ROOT}")"
if (( available_bytes < 300 * 1024 * 1024 * 1024 )); then
  echo "${DB_ROOT} has less than 300 GiB available" >&2
  exit 1
fi
if ! sudo -n true; then
  echo "Passwordless sudo is required for perf and package setup" >&2
  exit 1
fi

sudo mkdir -p "${TOOLS_DIR}" "${DB_ROOT}/data" "${DB_ROOT}/screening" "${DB_ROOT}/traces" "${MANIFEST_DIR}"
sudo chown -R "${USER}:$(id -gn)" "${DB_ROOT}"

if ! command -v mpstat >/dev/null; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y sysstat
fi
for command in docker git java curl sha256sum perf mpstat taskset; do
  command -v "${command}" >/dev/null || {
    echo "Missing required command: ${command}" >&2
    exit 1
  }
done

docker pull "${MYSQL_IMAGE}"
docker pull "${MONGO_IMAGE}"
docker pull "${BENCHBASE_BUILD_IMAGE}"
docker pull "${BENCHBASE_JRE_IMAGE}"
mysql_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${MYSQL_IMAGE}")"
mongo_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${MONGO_IMAGE}")"
benchbase_build_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${BENCHBASE_BUILD_IMAGE}")"
benchbase_jre_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${BENCHBASE_JRE_IMAGE}")"

benchbase_src="${TOOLS_DIR}/benchbase-src"
if [[ ! -d "${benchbase_src}/.git" ]]; then
  git clone --depth 1 --branch "${BENCHBASE_TAG}" "${BENCHBASE_REPO}" "${benchbase_src}"
fi
if [[ "$(git -C "${benchbase_src}" rev-parse HEAD)" != "${BENCHBASE_COMMIT}" ]]; then
  echo "BenchBase checkout is not ${BENCHBASE_COMMIT}" >&2
  exit 1
fi
if [[ ! -f "${TOOLS_DIR}/benchbase-mysql/benchbase.jar" ]]; then
  docker run --rm \
    -v "${benchbase_src}:/workspace" \
    -w /workspace \
    "${BENCHBASE_BUILD_IMAGE}" \
    bash -lc 'git config --global --add safe.directory /workspace && mvn -B clean package -P mysql -DskipTests'
  sudo chown -R "${USER}:$(id -gn)" "${benchbase_src}"
  tar -xzf "${benchbase_src}/target/benchbase-mysql.tgz" -C "${TOOLS_DIR}"
  extracted="$(find "${TOOLS_DIR}" -maxdepth 1 -type d -name 'benchbase-mysql*' ! -name benchbase-mysql | head -1)"
  if [[ -n "${extracted}" ]]; then
    mv "${extracted}" "${TOOLS_DIR}/benchbase-mysql"
  fi
fi

ycsb_archive="${TOOLS_DIR}/ycsb-mongodb-binding-0.17.0.tar.gz"
if [[ ! -f "${ycsb_archive}" ]]; then
  curl --fail --location --retry 3 --output "${ycsb_archive}" "${YCSB_URL}"
fi
printf '%s  %s\n' "${YCSB_SHA256}" "${ycsb_archive}" | sha256sum --check
if [[ ! -x "${TOOLS_DIR}/ycsb-mongodb-binding-0.17.0/bin/ycsb" ]]; then
  tar -xzf "${ycsb_archive}" -C "${TOOLS_DIR}"
fi

python3 - "${MANIFEST_DIR}/software.json" "${mysql_digest}" "${mongo_digest}" \
  "${benchbase_build_digest}" "${benchbase_jre_digest}" <<'PY'
import json
import platform
import sys
from pathlib import Path

out, mysql_digest, mongo_digest, benchbase_build_digest, benchbase_jre_digest = sys.argv[1:]
Path(out).write_text(json.dumps({
    "host": platform.node(),
    "mysql_image": mysql_digest,
    "mongo_image": mongo_digest,
    "benchbase_build_image": benchbase_build_digest,
    "benchbase_jre_image": benchbase_jre_digest,
    "benchbase_tag": "v2023",
    "benchbase_commit": "b4b36683afdf79dd8bf70b95199b874c68218975",
    "ycsb_version": "0.17.0",
    "ycsb_sha256": "6a054a706812269c80bfc6ed1e83457990c1c60b01f5083c873aaed05577e30d",
}, indent=2) + "\n")
PY

sudo perf stat -x, -C 0 -e cycles,instructions,stalled-cycles-backend -- sleep 1 \
  2>"${MANIFEST_DIR}/perf-smoke.csv"
if grep -q '<not supported>' "${MANIFEST_DIR}/perf-smoke.csv"; then
  echo "Required AMD perf events are not supported" >&2
  exit 1
fi

cat "${MANIFEST_DIR}/software.json"
