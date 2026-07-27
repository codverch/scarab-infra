#!/bin/bash
# McPAT/CACTI for --power_intf_on 1. Prefer binaries staged under scarab_stage
# (always inside the root_dir bind-mount). Fall back to root_dir/toolchain/bin/.
_power_bin() {
  local name="$1"
  local candidate
  for candidate in \
    "$HOME"/scarab_stage/*/scarab/bin/"$name" \
    "$HOME/toolchain/bin/$name"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

mcpat="$(_power_bin mcpat)" && export MCPAT_BIN="$mcpat"
cacti="$(_power_bin cacti)" && export CACTI_BIN="$cacti"
