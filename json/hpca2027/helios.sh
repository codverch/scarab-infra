#!/usr/bin/env bash
# Helios fusion only: no warmup; each simpoint runs to EOF (full trace)
# =============================================================================
# Discovers every app under TRACES_DIR with traces_simp/trace/*.zip.
#   --full_warmup 0
#   --inst_limit  = max SP size in the suite + 1M (Scarab stops at EOF on shorter zips)
#
# Helios confidence knobs (Scarab defaults, set explicitly):
#   --helios_confidence_threshold 150
#   --helios_confidence_increment 10
#   --helios_confidence_decrement 10
#   --helios_fusion_window        64
#
# Experiment : simulations/helios/{app}/{simpoint}/   (after finalize; no logs/config nesting)
# Descriptor : helios.json   (config: helios only)
#
# Usage:
#   ./helios.sh              # fast: register + sim + finalize (no rebuild)
#   ./helios.sh --build      # slow: also rebuild Scarab first
#   ./helios.sh --dry-run
#   ./helios.sh --finalize
#   ./helios.sh --status

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/helios"
DESCRIPTOR_JSON="${HPCA_DIR}/helios.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/helios"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(helios)
# Empty = all apps under TRACES_DIR.
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
main_full_trace "$@"
