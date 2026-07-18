#!/usr/bin/env bash
# Unbounded-distance ideal fusion pass-2: simulate with pass-1 candidates
# =============================================================================
# Requires Scarab branch: hpca2027-unbounded-distance-ideal-fusion
# Discovers every app under TRACES_DIR with traces_simp/trace/*.zip.
#   --full_warmup 0
#   --inst_limit  = max SP size in the suite + 1M (Scarab stops at EOF on shorter zips)
#   --ideal_fusion_pass 2
#   --ideal_fusion_log /dev/shm/baseline/ideal_fusion_candidates_unbounded/{app}/{simpoint}.csv
#
# Requires unbounded pass-1 candidates under:
#   /dev/shm/baseline/ideal_fusion_candidates_unbounded/
#
# Results (commit-friendly, after finalize):
#   simulations/ideal-fusion-unbounded-pass2/{app}/{simpoint}/
#
# Descriptor : ideal_fusion_unbounded_pass2.json   (config: pass2)
#
# Usage:
#   ./ideal_fusion_unbounded_pass2.sh              # register + sim + finalize (no rebuild)
#   ./ideal_fusion_unbounded_pass2.sh --build      # also rebuild Scarab first
#   ./ideal_fusion_unbounded_pass2.sh --dry-run
#   ./ideal_fusion_unbounded_pass2.sh --check-candidates
#   ./ideal_fusion_unbounded_pass2.sh --finalize
#   ./ideal_fusion_unbounded_pass2.sh --status

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/ideal_fusion_unbounded_pass2"
DESCRIPTOR_JSON="${HPCA_DIR}/ideal_fusion_unbounded_pass2.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/ideal-fusion-unbounded-pass2"
CANDIDATES_DIR="/dev/shm/baseline/ideal_fusion_candidates_unbounded"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(pass2)
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
# shellcheck source=/dev/null
source "${HPCA_DIR}/_ideal_fusion_common.sh"

pre_sim_hook() {
  check_ideal_fusion_candidates
}

case "${1:-}" in
  --check-candidates)
    cd "${INFRA_DIR}"
    discover_workloads
    check_ideal_fusion_candidates
    ;;
  *)
    main_full_trace "$@"
    ;;
esac
