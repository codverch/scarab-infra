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

# Prefer focal-built binaries mounted at $HOME/toolchain/{mcpat,cacti};
# otherwise fall back to host toolchain/bin layout.
if [ ! -e "$HOME/toolchain" ] && [ -d /tmp_home/application/scarab-infra/docker_bin/focal ]; then
  ln -sfn /tmp_home/application/scarab-infra/docker_bin/focal "$HOME/toolchain"
elif [ ! -e "$HOME/toolchain" ] && [ -d /tmp_home/application/toolchain ]; then
  ln -sfn /tmp_home/application/toolchain "$HOME/toolchain"
fi

resolve_power_bin() {
  local name="$1" cand
  for cand in \
      "$HOME/toolchain/$name" \
      "$HOME/toolchain/bin/$name" \
      "/tmp_home/application/scarab-infra/docker_bin/focal/$name" \
      "/tmp_home/application/toolchain/bin/$name" \
      "/usr/local/bin/$name"; do
    if [ -x "$cand" ]; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}

if [ -z "${MCPAT_BIN:-}" ] || [ ! -x "${MCPAT_BIN}" ]; then
  MCPAT_BIN="$(resolve_power_bin mcpat)" || MCPAT_BIN="${HOME}/toolchain/bin/mcpat"
fi
if [ -z "${CACTI_BIN:-}" ] || [ ! -x "${CACTI_BIN}" ]; then
  CACTI_BIN="$(resolve_power_bin cacti)" || CACTI_BIN="${HOME}/toolchain/bin/cacti"
fi
export MCPAT_BIN CACTI_BIN
[ -x "$MCPAT_BIN" ] || echo "WARNING: mcpat not found; --power_intf_on will produce no power data" >&2
[ -x "$CACTI_BIN" ] || echo "WARNING: cacti not found; --power_intf_on will produce no power data" >&2

if [ -f "/usr/local/bin/workload_user_entrypoint.sh" ]; then
  source /usr/local/bin/workload_user_entrypoint.sh
fi
