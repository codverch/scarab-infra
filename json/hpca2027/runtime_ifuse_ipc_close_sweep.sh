#!/usr/bin/env bash
# Runtime I-Fuse IPC-close sweep (match PGO-ish knobs)
# =============================================================================
# Goal: push runtime I-Fuse IPC toward hpca2027-ifuse-pgo by sweeping:
#   - FCT hash bits (10 / 16 / 22)   # PGO default is 22
#   - training table size (32/64/128 x 4)
#   - training insert threshold (100 / 500 / 1000 / 2000)
#   - confidence (insert conf, predict threshold, mispred penalty)
#   - APT match policy (0 / 1)
#
# Apps: graph + DB. Excludes agentic (hang-prone) and community/CC (mapper crash).
#
# Experiment : simulations/runtime-ifuse-ipc-close-sweep/{config}/...
# Descriptor : runtime_ifuse_ipc_close_sweep.json
#
# Usage:
#   ./runtime_ifuse_ipc_close_sweep.sh              # register + sim + finalize
#   ./runtime_ifuse_ipc_close_sweep.sh --build
#   ./runtime_ifuse_ipc_close_sweep.sh --dry-run
#   ./runtime_ifuse_ipc_close_sweep.sh --status

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/runtime_ifuse_ipc_close_sweep"
DESCRIPTOR_JSON="${HPCA_DIR}/runtime_ifuse_ipc_close_sweep.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/runtime-ifuse-ipc-close-sweep"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(
  rt_hb10_tt32x4_th1000
  rt_hb16_tt32x4_th1000
  rt_hb22_tt32x4_th1000
  rt_hb22_tt64x4_th1000
  rt_hb22_tt128x4_th1000
  rt_hb22_tt32x4_th100
  rt_hb22_tt32x4_th500
  rt_hb22_tt32x4_th2000
  rt_hb22_tt32x4_th1000_ins700
  rt_hb22_tt32x4_th1000_thr550
  rt_hb22_tt32x4_th1000_pen25
  rt_hb22_tt32x4_th1000_apt1
  rt_hb22_tt64x4_th500_ins700
)
KEEP_CONFIG_NESTING=1
# Agentic hang / community+CC mapper crashes from prior sweep.
EXCLUDE_APPS=(
  appworld
  core_bench
  terminal_bench
  mlgym_fmnist
  community
  connected_components
)

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
main_full_trace "$@"
