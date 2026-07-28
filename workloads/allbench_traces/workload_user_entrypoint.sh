#!/bin/bash
# McPAT/CACTI for --power_intf_on 1.
# Prefer docker-compatible builds, then $HOME/toolchain/bin (host bind-mount),
# then staged scarab_stage binaries.
_resolve_power_tool() {
  local name="$1"
  local candidate
  for candidate in \
    "/dev/shm/baseline/mcpat_build/${name}" \
    "${HOME}/toolchain/bin/${name}" \
    "/tmp_home/application/toolchain/bin/${name}"; do
    if [ -x "${candidate}" ]; then
      printf '%s' "${candidate}"
      return 0
    fi
  done
  for bindir in "${HOME}"/scarab_stage/*/scarab/bin; do
    candidate="${bindir}/${name}"
    if [ -x "${candidate}" ]; then
      printf '%s' "${candidate}"
      return 0
    fi
  done
  return 1
}

mcpat_path="$(_resolve_power_tool mcpat)" || true
if [ -n "${mcpat_path}" ]; then
  export MCPAT_BIN="${mcpat_path}"
fi

cacti_path="$(_resolve_power_tool cacti)" || true
if [ -n "${cacti_path}" ]; then
  export CACTI_BIN="${cacti_path}"
fi

echo "[workload_user_entrypoint] MCPAT_BIN=${MCPAT_BIN:-unset} CACTI_BIN=${CACTI_BIN:-unset}"
