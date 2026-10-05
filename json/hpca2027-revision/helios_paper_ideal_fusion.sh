#!/usr/bin/env bash
# HPCA2027 revision: ideal load fusion on the Helios MICRO'22 paper baseline
# =============================================================================
# Processor: scarab/src/PARAMS.helios_paper (paper Table II, Ramulator DRAM),
# read from the scarab working tree at launch. Fusion window = the 352-entry ROB.
#
# Two passes, run in order:
#   pass 1  logs every fusible load pair to tmpfs
#           (/dev/shm/baseline/ideal_fusion_candidates/helios_paper/<app>/20.csv.gz);
#           it does not change timing, so it is also the no-fusion baseline
#   pass 2  replays those pairs and models their fusion
#
# Workloads: deepsjeng_s exchange2_s gcc_s gcc_s_2 gcc_s_3 leela_s mcf_s
#            omnetpp_s xalancbmk_s
# Each trace is a single fixed Helios region; each run warms up for 20M
# instructions from instruction 1, then measures the next 100M.
#
# Usage:
#   ./json/hpca2027-revision/helios_paper_ideal_fusion.sh             # register + build + pass 1 + pass 2
#   ./json/hpca2027-revision/helios_paper_ideal_fusion.sh --pass2     # pass 2 only (candidates must exist)
#   ./json/hpca2027-revision/helios_paper_ideal_fusion.sh --register  # only register traces in workloads_db
#   ./json/hpca2027-revision/helios_paper_ideal_fusion.sh --status
#   ./json/hpca2027-revision/helios_paper_ideal_fusion.sh --package   # -> scarab/src/hpca2027-revision/helios-paper-config-ideal-fusion/<app>/ + speedup plot
#
# Put --stores first (e.g. `--stores`, `--stores --package`) to also fuse store
# pairs (--ideal_fusion_stores 1: no store in between, as in Helios). It uses the
# *_stores_pass{1,2} descriptors, candidates under helios_paper_stores/, and
# packages into helios-paper-config-ideal-fusion-stores/.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VARIANT=""
if [[ "${1:-}" == "--stores" ]]; then
  VARIANT="_stores"
  shift
fi
PASS1="hpca2027-revision/helios_paper_ideal_fusion${VARIANT}_pass1"
PASS2="hpca2027-revision/helios_paper_ideal_fusion${VARIANT}_pass2"
CAND_SUBDIR="helios_paper${VARIANT}"
OUT_DIR="helios-paper-config-ideal-fusion${VARIANT//_/-}"
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
  if [[ ! -f "${SCARAB_PATH}/src/PARAMS.helios_paper" ]]; then
    echo "ERROR: ${SCARAB_PATH}/src/PARAMS.helios_paper not found" >&2
    exit 1
  fi
}

check_candidates() {
  local app missing=0
  for app in "${APPS[@]}"; do
    if [[ ! -s "${CANDIDATES_DIR}/${CAND_SUBDIR}/${app}/20.csv.gz" ]]; then
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
  mkdir -p "${ROOT_DIR}" "${CANDIDATES_DIR}/${CAND_SUBDIR}"
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

main() {
  cd "${INFRA_DIR}"
  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi
  case "${1:-}" in
    "")         run_pass1 && run_pass2 ;;
    --pass2)    run_pass2 ;;
    --register) "${INFRA_DIR}/json/hpca2027-revision/helios_paper_ifuse.sh" --register ;;
    --status)   ./sci --status "${PASS1}"; ./sci --status "${PASS2}" ;;
    --package)
      python3 "${INFRA_DIR}/json/hpca2027-revision/package_helios_paper_ideal_fusion_results.py" \
        --pass1 "${PASS1_JSON}" --pass2 "${INFRA_DIR}/json/${PASS2}.json" \
        --out "${SCARAB_PATH}/src/hpca2027-revision/${OUT_DIR}"
      python3 "${INFRA_DIR}/json/hpca2027-revision/plot_helios_paper_ideal_fusion_speedup.py"
      ;;
    -h|--help)  sed -n '2,29p' "${SCRIPT}" ;;
    *)          echo "Unknown option: $1" >&2; exit 1 ;;
  esac
}

main "$@"
