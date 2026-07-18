#!/usr/bin/env bash
# Shared ideal-fusion helpers for hpca2027 pass1/pass2 launchers.

CANDIDATES_DIR="${CANDIDATES_DIR:-/dev/shm/baseline/ideal_fusion_candidates}"

ensure_candidates_dir() {
  mkdir -p "${CANDIDATES_DIR}"
  echo "Candidate output: ${CANDIDATES_DIR}/{app}/{simpoint}.csv"
}

# List expected candidate CSV paths for workloads under TRACES_DIR.
_expected_candidate_csvs() {
  python3 - "${TRACES_DIR}" "${CANDIDATES_DIR}" "${WORKLOADS[@]}" <<'PY'
import sys
from pathlib import Path

traces_dir = Path(sys.argv[1])
candidates_dir = Path(sys.argv[2])
workloads = sys.argv[3:]

for app in workloads:
    zdir = traces_dir / app / "traces_simp" / "trace"
    if not zdir.is_dir():
        continue
    for z in sorted(zdir.glob("*.zip"), key=lambda p: (0, int(p.stem)) if p.stem.isdigit() else (1, p.stem)):
        if not z.stem.isdigit():
            continue
        print(candidates_dir / app / f"{z.stem}.csv")
PY
}

check_ideal_fusion_candidates() {
  local missing=0 path
  ensure_candidates_dir
  echo "Checking ideal-fusion candidates under ${CANDIDATES_DIR}..."
  while IFS= read -r path; do
    [[ -z "${path}" ]] && continue
    if [[ ! -s "${path}" ]]; then
      echo "  MISSING: ${path}" >&2
      missing=$((missing + 1))
    fi
  done < <(_expected_candidate_csvs)
  if [[ "${missing}" -gt 0 ]]; then
    echo "ERROR: ${missing} candidate CSV(s) missing. Run ideal_fusion_pass1.sh first." >&2
    exit 1
  fi
  echo "All expected candidate CSVs present."
}

verify_ideal_fusion_candidates() {
  local missing=0 path total=0
  while IFS= read -r path; do
    [[ -z "${path}" ]] && continue
    total=$((total + 1))
    if [[ ! -s "${path}" ]]; then
      echo "  MISSING: ${path}" >&2
      missing=$((missing + 1))
    fi
  done < <(_expected_candidate_csvs)
  echo "Candidates: ${total} expected, $((total - missing)) written, ${missing} missing"
  if [[ "${missing}" -gt 0 ]]; then
    echo "WARN: some pass-1 candidate CSVs are missing or empty." >&2
    return 1
  fi
  return 0
}
