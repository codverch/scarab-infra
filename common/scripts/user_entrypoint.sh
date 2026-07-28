#!/bin/bash
#set -x #echo on

export tmpdir="/tmp_home"
# Required by run_memtrace_single_simpoint.sh; baked into full images via
# Dockerfile.common, but missing from partial-cache images.
export trace_home="${trace_home:-/simpoint_traces}"
export SCARAB_ENABLE_PT_MEMTRACE=1
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Resolve DynamoRIO: prefer image-baked install, then scarab HOME mount, then application mount.
if [ -z "${DYNAMORIO_HOME:-}" ] || [ ! -e "${DYNAMORIO_HOME}/lib64/release/libdynamorio.so" ]; then
  if [ -e "$tmpdir/DynamoRIO-Linux-10.0.0/exports/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="$tmpdir/DynamoRIO-Linux-10.0.0/exports"
  elif [ -e "$tmpdir/DynamoRIO-Linux-10.0.0/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="$tmpdir/DynamoRIO-Linux-10.0.0"
  elif [ -e "${HOME}/build/opt/deps/dynamorio/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="${HOME}/build/opt/deps/dynamorio"
  elif [ -e "/tmp_home/application/scarab/src/build/opt/deps/dynamorio/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="/tmp_home/application/scarab/src/build/opt/deps/dynamorio"
  else
    export DYNAMORIO_HOME="$tmpdir/DynamoRIO-Linux-10.0.0"
  fi
fi

# Resolve PIN similarly.
if [ -z "${PIN_ROOT:-}" ] || [ ! -d "${PIN_ROOT}" ]; then
  if [ -d "$tmpdir/pin-3.15-98253-gb56e429b1-gcc-linux" ]; then
    export PIN_ROOT="$tmpdir/pin-3.15-98253-gb56e429b1-gcc-linux"
  elif [ -d "${HOME}/pin-3.15" ]; then
    export PIN_ROOT="${HOME}/pin-3.15"
  elif [ -d "/tmp_home/application/pin-3.15" ]; then
    export PIN_ROOT="/tmp_home/application/pin-3.15"
  else
    export PIN_ROOT="$tmpdir/pin-3.15-98253-gb56e429b1-gcc-linux"
  fi
fi

export LD_LIBRARY_PATH="${PIN_ROOT}/extras/xed-intel64/lib:${PIN_ROOT}/intel64/runtime/pincrt:${DYNAMORIO_HOME}/lib64/release:${LD_LIBRARY_PATH:-}"

# McPAT / CACTI for --power_intf_on. root_dir is bind-mounted as $HOME, so
# expose toolchain there when it only exists via the application_dir mount.
if [ ! -e "$HOME/toolchain" ] && [ -d /tmp_home/application/toolchain ]; then
  ln -sfn /tmp_home/application/toolchain "$HOME/toolchain"
fi
export MCPAT_BIN="${MCPAT_BIN:-$HOME/toolchain/bin/mcpat}"
export CACTI_BIN="${CACTI_BIN:-$HOME/toolchain/bin/cacti}"

if [ -f "/usr/local/bin/workload_user_entrypoint.sh" ]; then
  source /usr/local/bin/workload_user_entrypoint.sh
fi
