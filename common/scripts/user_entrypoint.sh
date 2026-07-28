#!/bin/bash
#set -x #echo on

export tmpdir="/tmp_home"
export DYNAMORIO_HOME=$tmpdir/DynamoRIO-Linux-10.0.0/
export PIN_ROOT=$tmpdir/pin-3.15-98253-gb56e429b1-gcc-linux
export SCARAB_ENABLE_PT_MEMTRACE=1
export LD_LIBRARY_PATH=$tmpdir/pin-3.15-98253-gb56e429b1-gcc-linux/extras/xed-intel64/lib
export LD_LIBRARY_PATH=$tmpdir/pin-3.15-98253-gb56e429b1-gcc-linux/intel64/runtime/pincrt:$LD_LIBRARY_PATH
export LD_LIBRARY_PATH=$DYNAMORIO_HOME/lib64/release:$LD_LIBRARY_PATH

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

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
