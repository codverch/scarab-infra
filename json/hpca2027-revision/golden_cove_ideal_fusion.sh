#!/usr/bin/env bash
# HPCA2027 revision: ideal load fusion + no-fusion baseline on Golden Cove, SPEC17 speed int
# =============================================================================
# Processor: scarab/src/PARAMS.golden_cove -- the same core as the datacenter
# runs (1 L1D read port, 512-entry ROB, 6-wide), so SPEC and datacenter ideal
# fusion speedups are directly comparable. Fusion window = 512 (the default).
#
# Three runs, in order:
#   pass 1    logs every fusible load pair to tmpfs
#             (/dev/shm/baseline/ideal_fusion_candidates/golden_cove/<app>/20.csv.gz)
#   pass 2    replays those pairs and models their fusion
#   baseline  no fusion at all (--ideal_fusion_pass 0), launched after pass 2
#
# Workloads: deepsjeng_s exchange2_s gcc_s gcc_s_2 gcc_s_3 leela_s mcf_s
#            omnetpp_s perlbench_s perlbench_s_2 perlbench_s_3 xalancbmk_s
#            xz_s xz_s_2 (Helios fixed regions from
#            huggingface.co/datasets/harry1332/helios-spec2017-fixed-region-20261002,
#            at /dev/shm/baseline/<app>/traces/simp/20.zip and registered in
#            workloads_db.json). 20M warmup, then 100M measured.
#
# Usage:
#   ./json/hpca2027-revision/golden_cove_ideal_fusion.sh              # build + pass 1 + pass 2 + baseline
#   ./json/hpca2027-revision/golden_cove_ideal_fusion.sh --pass2      # pass 2 + baseline (candidates must exist)
#   ./json/hpca2027-revision/golden_cove_ideal_fusion.sh --baseline   # baseline only
#   ./json/hpca2027-revision/golden_cove_ideal_fusion.sh --status

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PASS1="hpca2027-revision/golden_cove_ideal_fusion_pass1"
PASS2="hpca2027-revision/golden_cove_ideal_fusion_pass2"
BASELINE="hpca2027-revision/golden_cove_baseline"
PASS1_JSON="${INFRA_DIR}/json/${PASS1}.json"
SCARAB_REF="${SCARAB_REF:-hpca2027-revision-ideal-fusion}"

desc() { python3 -c "import json; print(json.load(open('${PASS1_JSON}'))['$1'])"; }
SCARAB_PATH="$(desc scarab_path)"
ROOT_DIR="$(desc root_dir)"
CANDIDATES_DIR="$(desc application_dir)"
APPS=($(python3 -c "import json; print(' '.join(json.load(open('${PASS1_JSON}'))['simulations'][0]['workload']))"))

check_scarab_ref() {
  local branch
  branch="$(git -C "${SCARAB_PATH}" rev-parse --abbrev-ref HEAD)"
  if [[ "${branch}" != "${SCARAB_REF}" ]]; then
    echo "ERROR: ${SCARAB_PATH} is on '${branch}', expected '${SCARAB_REF}'" >&2
    exit 1
  fi
  echo "Scarab: ${SCARAB_REF} @ $(git -C "${SCARAB_PATH}" rev-parse --short HEAD)"
  if [[ ! -f "${SCARAB_PATH}/src/PARAMS.golden_cove" ]]; then
    echo "ERROR: ${SCARAB_PATH}/src/PARAMS.golden_cove not found" >&2
    exit 1
  fi
}

check_candidates() {
  local app missing=0
  for app in "${APPS[@]}"; do
    if [[ ! -s "${CANDIDATES_DIR}/golden_cove/${app}/20.csv.gz" ]]; then
      echo "ERROR: missing pass-1 candidates for ${app}" >&2
      missing=1
    fi
  done
  [[ "${missing}" == "0" ]]
}

run_pass1() {
  check_scarab_ref
  # Registration and image tagging are shared with the I-Fuse launcher.
  "${INFRA_DIR}/json/hpca2027-revision/helios_paper_ifuse.sh" --register
  mkdir -p "${ROOT_DIR}" "${CANDIDATES_DIR}/golden_cove"
  if ! docker image inspect "allbench_traces:$(git -C "${INFRA_DIR}" rev-parse --short HEAD)" >/dev/null 2>&1; then
    ./sci --build-image allbench_traces
  fi
  ./sci --build-scarab "${PASS1}"
  ./sci --sim "${PASS1}"
}

run_pass2() {
  check_scarab_ref
  check_candidates
  ./sci --sim "${PASS2}"
}

run_baseline() {
  check_scarab_ref
  ./sci --sim "${BASELINE}"
}

main() {
  cd "${INFRA_DIR}"
  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi
  case "${1:-}" in
    "")         run_pass1 && run_pass2 && run_baseline ;;
    --pass2)    run_pass2 && run_baseline ;;
    --baseline) run_baseline ;;
    --register) "${INFRA_DIR}/json/hpca2027-revision/helios_paper_ifuse.sh" --register ;;
    --status)   ./sci --status "${PASS1}"; ./sci --status "${PASS2}"; ./sci --status "${BASELINE}" ;;
    -h|--help)  sed -n '2,26p' "${SCRIPT}" ;;
    *)          echo "Unknown option: $1" >&2; exit 1 ;;
  esac
}

main "$@"
