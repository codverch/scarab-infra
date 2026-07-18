#!/usr/bin/env bash
# Runtime I-Fuse only: no warmup; each simpoint runs to EOF (full trace)
# =============================================================================
# Discovers every app under TRACES_DIR with traces_simp/trace/*.zip.
#   --full_warmup 0
#   --inst_limit  = max SP size in the suite + 1M (Scarab stops at EOF on shorter zips)
#
# Experiment : simulations/runtime-ifuse/
# Descriptor : runtime_ifuse.json   (config: runtime_ifuse only — no baseline)
#
# Usage:
#   ./runtime_ifuse.sh              # register, build, sim, finalize
#   ./runtime_ifuse.sh --sim-only
#   ./runtime_ifuse.sh --dry-run
#   ./runtime_ifuse.sh --status
#   ./runtime_ifuse.sh --collect-stats
#   ./runtime_ifuse.sh --finalize
#   ./runtime_ifuse.sh --visualize

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
DESCRIPTOR="hpca2027/runtime_ifuse"
DESCRIPTOR_JSON="${HPCA_DIR}/runtime_ifuse.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/runtime-ifuse"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(runtime_ifuse)
# Empty = all apps under TRACES_DIR.
EXCLUDE_APPS=()

# shellcheck source=/dev/null
source "${HPCA_DIR}/_full_trace_common.sh"
main_full_trace "$@"
