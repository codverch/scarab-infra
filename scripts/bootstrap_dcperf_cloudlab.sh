#!/usr/bin/env bash

set -euo pipefail

readonly ROOT_DIR="${1:-/proj/datacntr-effcy-PG0/${USER}/hpca2027_dcperf}"
readonly DCPERF_COMMIT="4dc3b5e8836796fb7d80316f43a1147d052dc2e7"
readonly DOCKER_CONFIG="/etc/docker/daemon.json"

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify the operating system" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
  echo "Expected Ubuntu 22.04, found ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi

sudo -n true
sudo mkdir -p "${ROOT_DIR}/docker" "${ROOT_DIR}/src" "${ROOT_DIR}/logs"
sudo chown -R "${USER}:$(id -gn)" "${ROOT_DIR}"

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  docker.io \
  git \
  jq \
  linux-tools-common \
  python3 \
  python3-pip \
  python3-venv

kernel_tools="linux-tools-$(uname -r)"
if apt-cache show "${kernel_tools}" >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${kernel_tools}"
fi

desired_config="$(mktemp)"
trap 'rm -f "${desired_config}"' EXIT
jq -n \
  --arg data_root "${ROOT_DIR}/docker" \
  '{
    "data-root": $data_root,
    "default-ulimits": {
      "nofile": {"Name": "nofile", "Soft": 1048576, "Hard": 1048576}
    }
  }' >"${desired_config}"

if sudo test -e "${DOCKER_CONFIG}"; then
  if ! sudo cmp -s "${desired_config}" "${DOCKER_CONFIG}"; then
    echo "Refusing to overwrite conflicting ${DOCKER_CONFIG}" >&2
    sudo cat "${DOCKER_CONFIG}" >&2
    exit 1
  fi
else
  sudo install -D -m 0644 "${desired_config}" "${DOCKER_CONFIG}"
fi

limits_file="$(mktemp)"
printf '%s\n' \
  "${USER} soft nofile 1048576" \
  "${USER} hard nofile 1048576" >"${limits_file}"
sudo install -m 0644 "${limits_file}" /etc/security/limits.d/99-dcperf.conf
rm -f "${limits_file}"

sudo systemctl enable --now docker
sudo systemctl restart docker

dcperf_dir="${ROOT_DIR}/src/DCPerf"
if [[ -d "${dcperf_dir}/.git" ]]; then
  git -C "${dcperf_dir}" fetch --all --tags --prune
else
  git clone https://github.com/facebookresearch/DCPerf.git "${dcperf_dir}"
fi
git -C "${dcperf_dir}" checkout --detach "${DCPERF_COMMIT}"

venv_dir="${ROOT_DIR}/venv"
python3 -m venv "${venv_dir}"
"${venv_dir}/bin/python" -m pip install --upgrade 'pip==24.3.1'
"${venv_dir}/bin/python" -m pip install \
  'click==8.1.7' \
  'numpy==1.26.4' \
  'pandas==2.0.3' \
  'PyYAML==6.0.1' \
  'tabulate==0.9.0'

echo "DCPerf root: ${ROOT_DIR}"
echo "DCPerf commit: $(git -C "${dcperf_dir}" rev-parse HEAD)"
echo "DCPerf Python: ${venv_dir}/bin/python"
echo "Docker data root: $(sudo docker info --format '{{.DockerRootDir}}')"
echo "Docker server: $(sudo docker version --format '{{.Server.Version}}')"
echo "Open-file limit applies to new login sessions; run 'ulimit -n' after reconnecting."
