#!/usr/bin/env bash

set -euo pipefail

readonly ROOT_DIR="${1:-/proj/datacntr-effcy-PG0/${USER}/hpca2027_dcperf}"
readonly HHVM_URL="https://github.com/facebookresearch/DCPerf/releases/download/hhvm/hhvm-3.30-multplatform-binary-ubuntu.tar.xz"
readonly ARCHIVE="${ROOT_DIR}/downloads/hhvm-3.30-multplatform-binary-ubuntu.tar.xz"
readonly EXTRACT_DIR="${ROOT_DIR}/src/hhvm-3.30-ubuntu"
readonly HHVM_BIN="/usr/local/hphpi/legacy/bin/hhvm"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "This helper has only been validated for the x86_64 HHVM artifact" >&2
  exit 1
fi

sudo -n true
mkdir -p "${ROOT_DIR}/downloads" "${ROOT_DIR}/logs"

if [[ ! -s "${ARCHIVE}" ]]; then
  curl --fail --location --retry 3 --output "${ARCHIVE}.partial" "${HHVM_URL}"
  mv "${ARCHIVE}.partial" "${ARCHIVE}"
fi
sha256sum "${ARCHIVE}" | tee "${ROOT_DIR}/logs/hhvm-3.30-ubuntu.sha256"

if [[ ! -x "${HHVM_BIN}" ]]; then
  mkdir -p "${EXTRACT_DIR}"
  tar -Jxf "${ARCHIVE}" -C "${EXTRACT_DIR}"
  mapfile -t installers < <(find "${EXTRACT_DIR}" -type f -name pour-hhvm.sh)
  if [[ "${#installers[@]}" -ne 1 ]]; then
    echo "Expected one pour-hhvm.sh, found ${#installers[@]}" >&2
    exit 1
  fi
  installer="${installers[0]}"
  sudo bash -lc "cd '$(dirname "${installer}")'; exec ./pour-hhvm.sh"
fi

test -x "${HHVM_BIN}"
LD_LIBRARY_PATH="/opt/local/hhvm-3.30/lib:${LD_LIBRARY_PATH:-}" "${HHVM_BIN}" --version
