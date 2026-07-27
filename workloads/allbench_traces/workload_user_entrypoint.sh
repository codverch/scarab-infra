#!/bin/bash
# McPAT/CACTI for --power_intf_on 1.
# Containers must see MCPAT_BIN/CACTI_BIN. Prefer $HOME/toolchain/bin (bind-mounted
# from the host toolchain). Fall back to binaries staged under scarab_stage.
if [ -x "$HOME/toolchain/bin/mcpat" ]; then
  export MCPAT_BIN="$HOME/toolchain/bin/mcpat"
elif [ -z "${MCPAT_BIN:-}" ] || [ ! -x "${MCPAT_BIN}" ]; then
  for candidate in "$HOME"/scarab_stage/*/scarab/bin/mcpat; do
    if [ -x "$candidate" ]; then
      export MCPAT_BIN="$candidate"
      break
    fi
  done
fi

if [ -x "$HOME/toolchain/bin/cacti" ]; then
  export CACTI_BIN="$HOME/toolchain/bin/cacti"
elif [ -z "${CACTI_BIN:-}" ] || [ ! -x "${CACTI_BIN}" ]; then
  for candidate in "$HOME"/scarab_stage/*/scarab/bin/cacti; do
    if [ -x "$candidate" ]; then
      export CACTI_BIN="$candidate"
      break
    fi
  done
fi
