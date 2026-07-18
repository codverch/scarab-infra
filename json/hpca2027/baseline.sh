#!/usr/bin/env bash
# Baseline only (I-Fuse off): no warmup; each simpoint runs to EOF (full trace)
# =============================================================================
# Discovers every app under TRACES_DIR with traces_simp/trace/*.zip.
#   --full_warmup 0
#   --inst_limit  = max SP size in the suite + 1M (Scarab stops at EOF on shorter zips)
#
# Experiment : simulations/baseline/
# Descriptor : baseline.json   (config: baseline only)
#
# Usage:
#   ./baseline.sh              # register, build, sim, finalize
#   ./baseline.sh --sim-only
#   ./baseline.sh --dry-run
#   ./baseline.sh --status
#   ./baseline.sh --collect-stats
#   ./baseline.sh --finalize
#   ./baseline.sh --visualize

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/baseline"
DESCRIPTOR_JSON="${HPCA_DIR}/baseline.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/baseline"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(baseline)
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
main_full_trace "$@"
