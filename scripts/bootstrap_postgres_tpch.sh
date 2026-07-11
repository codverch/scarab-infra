#!/usr/bin/env bash

set -euo pipefail

readonly DB_ROOT="${DB_ROOT:-/mnt/hpca2027-db}"
readonly ROOT="${DB_ROOT}/postgres-tpch"
readonly TOOLS_DIR="${ROOT}/tools"
readonly MANIFEST_DIR="${ROOT}/manifests"
readonly POSTGRES_IMAGE="postgres:16.10-bookworm"
readonly TPCH_REPO="https://github.com/gregrahn/tpch-kit.git"
readonly TPCH_COMMIT="852ad0a5ee31ebefeed884cea4188781dd9613a3"

[[ "$(findmnt -n -o SOURCE --target "${DB_ROOT}" 2>/dev/null || true)" == "/dev/sdb" ]] || {
  echo "${DB_ROOT} is not mounted from /dev/sdb" >&2
  exit 1
}
for command in docker gcc git make python3; do
  command -v "${command}" >/dev/null || { echo "Missing command: ${command}" >&2; exit 1; }
done

mkdir -p "${TOOLS_DIR}" "${MANIFEST_DIR}" "${ROOT}/data" \
  "${ROOT}/generated" "${ROOT}/queries" "${ROOT}/screening" "${ROOT}/traces"

docker pull "${POSTGRES_IMAGE}"
postgres_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${POSTGRES_IMAGE}")"

tpch_src="${TOOLS_DIR}/tpch-kit"
if [[ ! -d "${tpch_src}/.git" ]]; then
  git clone "${TPCH_REPO}" "${tpch_src}"
fi
git -C "${tpch_src}" fetch --depth 1 origin "${TPCH_COMMIT}"
git -C "${tpch_src}" checkout --detach "${TPCH_COMMIT}"
[[ "$(git -C "${tpch_src}" rev-parse HEAD)" == "${TPCH_COMMIT}" ]]

make -C "${tpch_src}/dbgen" clean
make -C "${tpch_src}/dbgen" MACHINE=LINUX DATABASE=POSTGRESQL
test -x "${tpch_src}/dbgen/dbgen"
test -x "${tpch_src}/dbgen/qgen"

python3 - "${MANIFEST_DIR}/software.json" "${postgres_digest}" <<'PY'
import json
import platform
import sys
from pathlib import Path

out, postgres_digest = sys.argv[1:]
Path(out).write_text(json.dumps({
    "host": platform.node(),
    "postgres_image": postgres_digest,
    "tpch_repo": "https://github.com/gregrahn/tpch-kit.git",
    "tpch_commit": "852ad0a5ee31ebefeed884cea4188781dd9613a3",
    "dbgen_machine": "LINUX",
    "qgen_database": "POSTGRESQL",
}, indent=2) + "\n")
PY

cat "${MANIFEST_DIR}/software.json"
