#!/bin/bash
#set -x #echo on

export tmpdir="/tmp_home"
export DYNAMORIO_HOME=$tmpdir/DynamoRIO-Linux-10.0.0/
export PIN_ROOT=$tmpdir/sde-external-9.44.0-2024-08-22-lin/pinkit
export SCARAB_ENABLE_PT_MEMTRACE=1
# PinPlay is opt-in: export SCARAB_ENABLE_PINPLAY=1 for pinball replay, which
# runs under SDE's own launcher. Enabling it here made every live exec-driven
# run use an SDE-linked tool that cannot run under plain `pin`.
# SDE_GDB_XML costs nothing when PinPlay is off, and sde_init() aborts without
# it the moment anyone does opt in. Derived from PIN_ROOT so an SDE bump needs
# no edit here.
export SDE_GDB_XML="$(dirname "$PIN_ROOT")/misc/gdb-xml"
export LD_LIBRARY_PATH=$tmpdir/sde-external-9.44.0-2024-08-22-lin/pinkit/extras/xed-intel64/lib
export LD_LIBRARY_PATH=$tmpdir/sde-external-9.44.0-2024-08-22-lin/pinkit/intel64/runtime/pincrt:$LD_LIBRARY_PATH
export LD_LIBRARY_PATH=$DYNAMORIO_HOME/lib64/release:$LD_LIBRARY_PATH

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

if [ -f "/usr/local/bin/workload_user_entrypoint.sh" ]; then
  source /usr/local/bin/workload_user_entrypoint.sh
fi

# Scarab's power model (--power_intf_on 1) shells out to McPAT and CACTI. They
# must be built for this image's glibc, so they live under the mounted home at
# $HOME/toolchain/bin; an explicit MCPAT_BIN/CACTI_BIN still wins.
if [ -z "${MCPAT_BIN:-}" ] && [ -x "$HOME/toolchain/bin/mcpat" ]; then
  export MCPAT_BIN="$HOME/toolchain/bin/mcpat"
fi
if [ -z "${CACTI_BIN:-}" ] && [ -x "$HOME/toolchain/bin/cacti" ]; then
  export CACTI_BIN="$HOME/toolchain/bin/cacti"
fi
