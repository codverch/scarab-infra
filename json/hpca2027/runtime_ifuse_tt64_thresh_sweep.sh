#!/usr/bin/env bash
# Runtime I-Fuse: 2x training table (64x4) + insert-threshold sweep
# =============================================================================
# Same thresholds as train_threshold_sweep (10 / 100 / 1000 / 10000), but with
# --ifuse_training_table_sets 64 (256 entries vs default 128).
#
# Experiment : simulations/runtime-ifuse-tt64-thresh-sweep/{config}/{app}/{sp}/
# Descriptor : runtime_ifuse_tt64_thresh_sweep.json
#
# Usage:
#   ./runtime_ifuse_tt64_thresh_sweep.sh              # register + sim + finalize
#   ./runtime_ifuse_tt64_thresh_sweep.sh --build
#   ./runtime_ifuse_tt64_thresh_sweep.sh --dry-run
#   ./runtime_ifuse_tt64_thresh_sweep.sh --finalize
#   ./runtime_ifuse_tt64_thresh_sweep.sh --status

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/runtime_ifuse_tt64_thresh_sweep"
DESCRIPTOR_JSON="${HPCA_DIR}/runtime_ifuse_tt64_thresh_sweep.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/runtime-ifuse-tt64-thresh-sweep"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(tt64_thresh_10 tt64_thresh_100 tt64_thresh_1000 tt64_thresh_10000)
KEEP_CONFIG_NESTING=1
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
main_full_trace "$@"
