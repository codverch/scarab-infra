#!/usr/bin/env bash
# HPCA2027 revision: baseline (no fusion) on the Helios MICRO'22 paper config
# =============================================================================
# Processor: scarab/src/PARAMS.helios_paper (paper Table II), read from the
# scarab working tree at launch.
#   helios_paper_baseline  hpca2027-revision-baseline, no fusion
#
# Workloads: deepsjeng_s exchange2_s gcc_s gcc_s_2 gcc_s_3 leela_s mcf_s
#            omnetpp_s xalancbmk_s
# Each trace is a single fixed Helios region; the first 20M instructions warm
# up and the next 100M are measured (--inst_limit 120000000 counts both).
#
# Usage:
#   ./json/hpca2027-revision/helios_paper_baseline.sh             # register + build (if needed) + sim
#   ./json/hpca2027-revision/helios_paper_baseline.sh --fetch     # only download missing traces to /dev/shm
#   ./json/hpca2027-revision/helios_paper_baseline.sh --register  # only register traces in workloads_db
#   ./json/hpca2027-revision/helios_paper_baseline.sh --build     # force a Scarab rebuild, then sim
#   ./json/hpca2027-revision/helios_paper_baseline.sh --status
#   ./json/hpca2027-revision/helios_paper_baseline.sh --package   # -> scarab/src/hpca2027-revision/helios-paper-config-baseline/<app>/

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESCRIPTOR="hpca2027-revision/helios_paper_baseline"
DESCRIPTOR_JSON="${INFRA_DIR}/json/${DESCRIPTOR}.json"
SCARAB_REF="${SCARAB_REF:-hpca2027-revision-baseline}"

desc() { python3 -c "import json,sys; print(json.load(open('${DESCRIPTOR_JSON}'))['$1'])"; }
SCARAB_PATH="$(desc scarab_path)"
TRACES_DIR="$(desc traces_dir)"
ROOT_DIR="$(desc root_dir)"
EXPERIMENT="$(desc experiment)"

# Download any missing Helios fixed-region trace to <traces_dir>/<app>/traces/simp/20.zip.
# /dev/shm is wiped on reboot, so rerun this after one.
HF_URL="https://huggingface.co/datasets/harry1332/helios-spec2017-fixed-region-20261002/resolve/main/spec2017/speed_int_helios"
fetch_traces() {
  local app dst
  for app in $(python3 -c "import json; print(' '.join(a for s in json.load(open('${DESCRIPTOR_JSON}'))['simulations'] for a in s['workload']))"); do
    dst="${TRACES_DIR}/${app}/traces/simp/20.zip"
    [[ -s "${dst}" ]] && continue
    echo "Downloading ${app}"
    mkdir -p "$(dirname "${dst}")"
    curl -fL --retry 3 -o "${dst}.part" "${HF_URL}/${app}/traces/simp/20.zip"
    mv "${dst}.part" "${dst}"
  done
}

# Register the raw traces (<traces_dir>/<app>/traces/simp/<id>.zip) under the
# <suite>/<subsuite>/<app> layout sci expects, using relative symlinks so they
# resolve inside the Docker trace mount, and add single-region workloads_db entries.
# Existing entries (e.g. from baseline.sh, which registers the same traces) are kept,
# but their warmup is raised to this descriptor's, since sci rejects a larger one.
register_traces() {
  python3 - "${DESCRIPTOR_JSON}" "${INFRA_DIR}/workloads/workloads_db.json" <<'PY'
import json, os, sys
from pathlib import Path

desc = json.loads(Path(sys.argv[1]).read_text())
db_path = Path(sys.argv[2])
db = json.loads(db_path.read_text())
traces = Path(desc["traces_dir"])
changed = False
for sim in desc["simulations"]:
    suite, subsuite, warmup = sim["suite"], sim["subsuite"], sim["warmup"]
    for app in sim["workload"]:
        zips = sorted((traces / app / "traces" / "simp").glob("*.zip"))
        if not zips:
            sys.exit(f"ERROR: no trace zip under {traces / app / 'traces/simp'}")
        link = traces / suite / subsuite / app
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            os.symlink(os.path.relpath(traces / app, link.parent), link)
        entry = {
            "simulation": {
                "prioritized_mode": "memtrace",
                "memtrace": {
                    "image_name": "allbench_traces",
                    "segment_size": 10000000,
                    "warmup": warmup,
                    "whole_trace_file": None,
                    "trace_type": "trace_then_cluster",
                },
            },
            "simpoints": [
                {"cluster_id": int(z.stem), "segment_id": 0, "weight": 1.0 / len(zips)}
                for z in zips
            ],
        }
        slot = db.setdefault(suite, {}).setdefault(subsuite, {})
        if app not in slot:
            slot[app] = entry
            changed = True
        mt = slot[app]["simulation"]["memtrace"]
        if (mt.get("warmup") or 0) < warmup:
            mt["warmup"] = warmup
            changed = True
        print(f"  {suite}/{subsuite}/{app}: {[z.name for z in zips]}")
if changed:
    db_path.write_text(json.dumps(db, indent=2, separators=(",", ":")))
    print(f"Updated {db_path}")
PY
}

# Image tags follow the scarab-infra git hash; retag the existing image after
# a commit instead of rebuilding it from scratch.
ensure_docker_image_tag() {
  local img existing
  img="allbench_traces:$(git -C "${INFRA_DIR}" rev-parse --short HEAD)"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    return 0
  fi
  existing="$(docker images allbench_traces --format '{{.Tag}}' | head -1 || true)"
  if [[ -z "${existing}" ]]; then
    ./sci --build-image allbench_traces
  else
    echo "Retagging allbench_traces:${existing} -> ${img}"
    docker tag "allbench_traces:${existing}" "${img}"
  fi
}

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

run_sim() {
  local force_build="${1:-0}"
  check_scarab_ref
  fetch_traces
  register_traces
  ensure_docker_image_tag
  mkdir -p "${ROOT_DIR}"
  if [[ "${force_build}" == "1" || ! -e "${INFRA_DIR}/scarab_builds/scarab_current.opt" ]]; then
    ./sci --build-scarab "${DESCRIPTOR}"
  fi
  ./sci --sim "${DESCRIPTOR}"
}

main() {
  cd "${INFRA_DIR}"
  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi
  case "${1:-}" in
    "")              run_sim 0 ;;
    --build)         run_sim 1 ;;
    --fetch)         fetch_traces ;;
    --register)      register_traces ;;
    --status)        ./sci --status "${DESCRIPTOR}" ;;
    --collect-stats) ./sci --collect-stats "${DESCRIPTOR}" ;;
    --package)
      python3 "${INFRA_DIR}/json/hpca2027-revision/package_helios_paper_baseline_results.py" \
        --descriptor "${DESCRIPTOR_JSON}"
      ;;
    -h|--help)       sed -n '2,21p' "${SCRIPT}" ;;
    *)               echo "Unknown option: $1" >&2; exit 1 ;;
  esac
}

main "$@"
