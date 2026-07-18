#!/usr/bin/env bash
# Runtime I-Fuse training-threshold sweep (obs before FCT insert)
# =============================================================================
# Sweeps --ifuse_training_insert_threshold over 10 / 100 / 1000 / 10000.
# Each retired LD1→LD2 PC pair is counted in the training table; only after
# N observations is it promoted into the Fusion Candidate Table.
#
# Experiment : simulations/runtime-ifuse-train-threshold-sweep/{config}/{app}/{sp}/
# Descriptor : runtime_ifuse_train_threshold_sweep.json
#
# Usage:
#   ./runtime_ifuse_train_threshold_sweep.sh              # register + sim + finalize
#   ./runtime_ifuse_train_threshold_sweep.sh --build      # also rebuild Scarab first
#   ./runtime_ifuse_train_threshold_sweep.sh --dry-run
#   ./runtime_ifuse_train_threshold_sweep.sh --finalize
#   ./runtime_ifuse_train_threshold_sweep.sh --status

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/runtime_ifuse_train_threshold_sweep"
DESCRIPTOR_JSON="${HPCA_DIR}/runtime_ifuse_train_threshold_sweep.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/runtime-ifuse-train-threshold-sweep"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(train_thresh_10 train_thresh_100 train_thresh_1000 train_thresh_10000)
# Keep {config}/{app}/{sp} so thresholds do not overwrite each other.
KEEP_CONFIG_NESTING=1
# Empty = all apps under TRACES_DIR.
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
main_full_trace "$@"
