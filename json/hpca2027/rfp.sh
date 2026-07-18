#!/usr/bin/env bash
# Register File Prefetch (RFP): baseline vs RFP, full-trace (no warmup)
# =============================================================================
# Requires Scarab on branch hpca2027-rfp (RFP_ON / --rfp_on knobs).
# Discovers every app under TRACES_DIR with traces_simp/trace/*.zip.
#   --full_warmup 0
#   --inst_limit  = max SP size in the suite + 1M (Scarab stops at EOF on shorter zips)
#
# Experiment : simulations/rfp/{config}/{app}/{simpoint}/
# Descriptor : rfp.json   (configs: baseline=--rfp_on 0, rfp=--rfp_on 1)
#
# Usage:
#   ./rfp.sh              # fast: register + sim + finalize (no rebuild)
#   ./rfp.sh --build      # slow: also rebuild Scarab first
#   ./rfp.sh --dry-run
#   ./rfp.sh --finalize
#   ./rfp.sh --status

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/rfp"
DESCRIPTOR_JSON="${HPCA_DIR}/rfp.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/rfp"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(baseline rfp)
# Keep {config}/{app}/{sp} so baseline and rfp do not overwrite each other.
KEEP_CONFIG_NESTING=1
# Empty = all apps under TRACES_DIR.
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"

pre_sim_hook() {
  local scarab_path branch
  scarab_path="$(python3 -c "import json; print(json.load(open('${DESCRIPTOR_JSON}'))['scarab_path'])")"
  if [[ ! -d "${scarab_path}/.git" ]]; then
    echo "ERROR: scarab_path is not a git repo: ${scarab_path}" >&2
    exit 1
  fi
  branch="$(git -C "${scarab_path}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "${branch}" != "hpca2027-rfp" ]]; then
    echo "WARN: Scarab is on '${branch}', expected 'hpca2027-rfp' for RFP." >&2
    echo "      cd ${scarab_path} && git checkout hpca2027-rfp && rebuild (--build)." >&2
  fi
}

main_full_trace "$@"
