#!/usr/bin/env bash
# HPCA 2027 revision: Golden Cove baseline, ROB 352 vs ROB 512.
# =============================================================================
# Workloads: Helios SPEC CPU2017 speed_int fixed-region traces
#   (huggingface.co/datasets/harry1332/helios-spec2017-fixed-region-20261002)
#   gcc_s gcc_s_2 gcc_s_3 leela_s mcf_s omnetpp_s xalancbmk_s
#
# Methodology (matches Helios): each trace is one fixed region; simulate
# 500M instructions from instruction 1 with no warmup.
#
# Configs (json/hpca2027-revision/baseline.json):
#   baseline-rob352  PARAMS.golden_cove + --node_table_size 352
#   baseline-rob512  PARAMS.golden_cove as-is (node_table_size 512)
#
# Usage:
#   ./baseline.sh                 # register traces + launch all sims
#   ./baseline.sh --register      # only register traces in workloads_db.json
#   ./baseline.sh --status        # sim progress
#   ./baseline.sh --package       # package results into the scarab repo
#   ./baseline.sh --kill          # kill running sims

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESCRIPTOR="hpca2027-revision/baseline"
TRACES_DIR="/dev/shm/baseline"
SUITE="spec2017"
SUBSUITE="speed_int_helios"
SEGMENT_SIZE=10000000   # drmemtrace chunk size (10M instructions per chunk)
CLUSTER_ID=20           # every Helios trace ships as traces/simp/20.zip
ROOT_DIR="/users/deepmish/hpca2027-revision"
SCARAB_PATH="/users/deepmish/scarab"
RESULTS_DIR="${SCARAB_PATH}/results/hpca2027-revision-baseline"
CONFIGS=(baseline-rob352 baseline-rob512)
APPS=(gcc_s gcc_s_2 gcc_s_3 leela_s mcf_s omnetpp_s xalancbmk_s)

# Lay the raw Helios traces out as <suite>/<subsuite>/<app>/traces/simp/<id>.zip
# (relative symlinks, no copies) and record them in workloads_db.json.
register_traces() {
  local app link_dir
  for app in "${APPS[@]}"; do
    if [[ ! -f "${TRACES_DIR}/${app}/traces/simp/${CLUSTER_ID}.zip" ]]; then
      echo "ERROR: missing trace ${TRACES_DIR}/${app}/traces/simp/${CLUSTER_ID}.zip" >&2
      exit 1
    fi
    link_dir="${TRACES_DIR}/${SUITE}/${SUBSUITE}/${app}"
    mkdir -p "${TRACES_DIR}/${SUITE}/${SUBSUITE}"
    ln -sfn "../../${app}" "${link_dir}"
  done

  python3 - "${INFRA_DIR}/workloads/workloads_db.json" "${SUITE}" "${SUBSUITE}" \
    "${SEGMENT_SIZE}" "${CLUSTER_ID}" "${APPS[@]}" <<'PY'
import json, sys
db_path, suite, subsuite, seg, cid, *apps = sys.argv[1:]
with open(db_path) as f:
    db = json.load(f)
sub = db.setdefault(suite, {}).setdefault(subsuite, {})
for app in apps:
    sub[app] = {
        "simulation": {
            "prioritized_mode": "memtrace",
            "memtrace": {
                "image_name": "allbench_traces",
                "segment_size": int(seg),
                "warmup": 0,
                "trace_type": "trace_then_cluster",
                "whole_trace_file": None,
            },
        },
        "simpoints": [{"cluster_id": int(cid), "segment_id": 0, "weight": 1.0}],
    }
with open(db_path, "w") as f:
    json.dump(db, f, indent=2, separators=(",", ":"))
print(f"Registered {len(apps)} workloads under {suite}/{subsuite}")
PY
}

package_results() {
  python3 "${INFRA_DIR}/json/hpca2027-revision/package_results.py" \
    --sim-root "${ROOT_DIR}/simulations" \
    --out "${RESULTS_DIR}" \
    --configs "${CONFIGS[@]}" \
    --apps "${APPS[@]}"
}

cd "${INFRA_DIR}"
case "${1:-}" in
  --register) register_traces ;;
  --status)   ./sci --status "${DESCRIPTOR}" ;;
  --kill)     ./sci --kill "${DESCRIPTOR}" ;;
  --package)  package_results ;;
  "")
    register_traces
    mkdir -p "${ROOT_DIR}"
    ./sci --sim "${DESCRIPTOR}"
    ;;
  *) sed -n '2,21p' "$0"; exit 1 ;;
esac
