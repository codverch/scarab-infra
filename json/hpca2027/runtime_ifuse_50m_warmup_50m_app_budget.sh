#!/usr/bin/env bash
# Runtime I-Fuse: 50M warmup + 50M measured **per app**, split across simpoints
# =============================================================================
# Scarab-infra applies one params string to every workload in a descriptor, so
# this launcher regenerates one descriptor per unique #simpoints (n), with:
#
#   full_warmup = 50e6 // n
#   inst_limit  = 2 * (50e6 // n)     # warmup + measure
#
# App totals (sum over SPs): ~50M warmup + ~50M measure.
# Excludes sssp_ego_fb (and any EXCLUDE_APPS).
#
# Experiment : simulations/runtime-ifuse-50m-app-budget/
# Master JSON: runtime_ifuse_50m_warmup_50m_app_budget.json  (status/stats/viz)
# Group JSONs: generated/runtime_ifuse_50m_app_budget_n{N}.json
#
# Usage:
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh              # register, build, sim, finalize
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh --sim-only
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh --dry-run    # print budget table + write JSONs
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh --status
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh --collect-stats
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh --finalize
#   ./runtime_ifuse_50m_warmup_50m_app_budget.sh --visualize

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HPCA_DIR="${INFRA_DIR}/json/hpca2027"
GEN_DIR="${HPCA_DIR}/generated"
MASTER_DESCRIPTOR="hpca2027/runtime_ifuse_50m_warmup_50m_app_budget"
MASTER_JSON="${HPCA_DIR}/runtime_ifuse_50m_warmup_50m_app_budget.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/runtime-ifuse-50m-app-budget"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(baseline runtime_ifuse)

# Apps to skip (short traces / not in this budget scheme).
EXCLUDE_APPS=(sssp_ego_fb)

APP_WARMUP_TOTAL=50000000
APP_MEASURE_TOTAL=50000000

cd "${INFRA_DIR}"

if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "${HOME}/miniconda3/etc/profile.d/conda.sh"
  conda activate scarabinfra 2>/dev/null || true
fi

usage() {
  cat <<'EOF'
Usage: runtime_ifuse_50m_warmup_50m_app_budget.sh [option]

  (default)          Register traces, build Scarab, generate per-n descriptors, sim, finalize
  --sim-only         Register + generate + sim + finalize (skip build)
  --dry-run          Print per-app budget table and write generated JSONs only
  --status           Show simulation status (master descriptor)
  --collect-stats    Collect stats only (master descriptor)
  --finalize         Flatten to {config}/{app}/{simpoint} and strip binaries/logs
  --visualize        Run descriptor visualization
  -h, --help         Show this help
EOF
}

is_excluded() {
  local app="$1"
  local x
  for x in "${EXCLUDE_APPS[@]}"; do
    [[ "${app}" == "${x}" ]] && return 0
  done
  return 1
}

# Discover apps under traces_dir (exclude suite symlink + EXCLUDE_APPS + non-trace dirs).
discover_workloads() {
  local d app
  WORKLOADS=()
  while IFS= read -r -d '' d; do
    app="$(basename "${d}")"
    if [[ "${app}" == .* ]]; then
      continue
    fi
    if is_excluded "${app}"; then
      continue
    fi
    if ! compgen -G "${d}/traces_simp/trace/*.zip" > /dev/null; then
      continue
    fi
    WORKLOADS+=("${app}")
  done < <(find "${TRACES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name "${SUITE}" -print0 | sort -z)
}

# Write generated/runtime_ifuse_50m_app_budget_n{N}.json for each unique n,
# and refresh the master JSON workload list. Prints the budget table.
generate_descriptors() {
  mkdir -p "${GEN_DIR}"
  discover_workloads
  if [[ ${#WORKLOADS[@]} -eq 0 ]]; then
    echo "ERROR: no workloads found under ${TRACES_DIR}" >&2
    exit 1
  fi

  python3 - "${TRACES_DIR}" "${MASTER_JSON}" "${GEN_DIR}" "${APP_WARMUP_TOTAL}" "${APP_MEASURE_TOTAL}" "${WORKLOADS[@]}" <<'PY'
import json, sys
from pathlib import Path
from collections import defaultdict

traces_dir = Path(sys.argv[1])
master_path = Path(sys.argv[2])
gen_dir = Path(sys.argv[3])
app_warmup = int(sys.argv[4])
app_measure = int(sys.argv[5])
workloads = sys.argv[6:]

by_n = defaultdict(list)
rows = []
for app in workloads:
    zdir = traces_dir / app / "traces_simp" / "trace"
    n = len(list(zdir.glob("*.zip"))) if zdir.is_dir() else 0
    if n <= 0:
        print(f"WARN: {app}: no simpoint zips, skipping", file=sys.stderr)
        continue
    warm = app_warmup // n
    meas = app_measure // n
    lim = warm + meas
    by_n[n].append(app)
    rows.append((app, n, warm, meas, lim))

print(f"{'app':16} {'n_sp':>4} {'warmup/SP':>12} {'measure/SP':>12} {'inst_limit':>12} {'app warm':>12} {'app meas':>12}")
print("-" * 92)
for app, n, warm, meas, lim in rows:
    print(f"{app:16} {n:4} {warm:12,} {meas:12,} {lim:12,} {warm*n:12,} {meas*n:12,}")

master = json.loads(master_path.read_text())
all_apps = [r[0] for r in rows]
master["simulations"][0]["workload"] = all_apps
# Master params are unused for --sim (group files own the split). Keep n=2 values as docs.
warm2 = app_warmup // 2
lim2 = warm2 + (app_measure // 2)
base_common = f"--icache_size 32768 --inst_limit {lim2} --full_warmup {warm2}"
master["configurations"]["baseline"]["params"] = (
    f"{base_common} --ifuse_fusion_distance 0 --ifuse_runtime_training_enabled 0"
)
master["configurations"]["runtime_ifuse"]["params"] = (
    f"{base_common} --ifuse_fusion_distance 512 --ifuse_apt_match_policy 0 "
    f"--ifuse_runtime_training_enabled 1 --ifuse_training_insert_threshold 1000 "
    f"--ifuse_fct_hash_bits 10"
)
master_path.write_text(json.dumps(master, indent=2) + "\n")
print(f"\nUpdated master: {master_path}")

# Clear old group files for this experiment prefix.
for old in gen_dir.glob("runtime_ifuse_50m_app_budget_n*.json"):
    old.unlink()

for n in sorted(by_n):
    apps = sorted(by_n[n])
    warm = app_warmup // n
    meas = app_measure // n
    lim = warm + meas
    desc = json.loads(master_path.read_text())
    desc["simulations"][0]["workload"] = apps
    desc["_comment"] = (
        f"Auto-generated: n_sp={n}; per-SP full_warmup={warm}, inst_limit={lim} "
        f"(app totals ~{app_warmup}+{app_measure})."
    )
    base = (
        f"--icache_size 32768 --inst_limit {lim} --full_warmup {warm} "
        f"--ifuse_fusion_distance 0 --ifuse_runtime_training_enabled 0"
    )
    ifuse = (
        f"--icache_size 32768 --inst_limit {lim} --full_warmup {warm} "
        f"--ifuse_fusion_distance 512 --ifuse_apt_match_policy 0 "
        f"--ifuse_runtime_training_enabled 1 --ifuse_training_insert_threshold 1000 "
        f"--ifuse_fct_hash_bits 10"
    )
    desc["configurations"]["baseline"]["params"] = base
    desc["configurations"]["runtime_ifuse"]["params"] = ifuse
    out = gen_dir / f"runtime_ifuse_50m_app_budget_n{n}.json"
    out.write_text(json.dumps(desc, indent=2) + "\n")
    print(f"Wrote {out.name}: n={n} warmup={warm:,} inst_limit={lim:,} apps={apps}")
PY
}

list_group_descriptors() {
  find "${GEN_DIR}" -maxdepth 1 -name 'runtime_ifuse_50m_app_budget_n*.json' | sort
}

register_traces() {
  discover_workloads
  if [[ ${#WORKLOADS[@]} -eq 0 ]]; then
    echo "ERROR: no workloads found under ${TRACES_DIR}" >&2
    exit 1
  fi
  echo "Registering ${#WORKLOADS[@]} workload(s): ${WORKLOADS[*]}"
  python -m scripts.register_local_traces \
    --traces-dir "${TRACES_DIR}" \
    --workloads "${WORKLOADS[@]}" \
    --warmup 0
}

write_experiment_gitignore() {
  mkdir -p "${EXPERIMENT_DIR}"
  cat > "${EXPERIMENT_DIR}/.gitignore" <<'EOF'
# Job / infrastructure noise (do not commit)
logs/
scarab_stage/
**/scarab_current*
**/scarab_*
**/*.warmup
**/ramulator.stat.out
**/PARAMS.in
EOF
}

finalize_results() {
  if [[ ! -d "${EXPERIMENT_DIR}" ]]; then
    echo "ERROR: experiment dir not found: ${EXPERIMENT_DIR}" >&2
    exit 1
  fi

  echo "Finalizing ${EXPERIMENT_DIR} (flatten + strip binaries/logs)..."
  write_experiment_gitignore

  for config in "${CONFIGS[@]}"; do
    local nested="${EXPERIMENT_DIR}/${config}/${SUITE}/${SUBSUITE}"
    if [[ ! -d "${nested}" ]]; then
      continue
    fi
    mkdir -p "${EXPERIMENT_DIR}/${config}"
    find "${nested}" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' app_dir; do
      local app dest
      app="$(basename "${app_dir}")"
      dest="${EXPERIMENT_DIR}/${config}/${app}"
      if [[ -e "${dest}" ]]; then
        echo "  merging ${app_dir} -> ${dest}"
        mkdir -p "${dest}"
        find "${app_dir}" -mindepth 1 -maxdepth 1 -print0 | while IFS= read -r -d '' sp; do
          local sp_name dest_sp
          sp_name="$(basename "${sp}")"
          dest_sp="${dest}/${sp_name}"
          if [[ -e "${dest_sp}" ]]; then
            rm -rf "${dest_sp}"
          fi
          mv "${sp}" "${dest_sp}"
        done
        rmdir "${app_dir}" 2>/dev/null || rm -rf "${app_dir}"
      else
        echo "  moving ${app_dir} -> ${dest}"
        mv "${app_dir}" "${dest}"
      fi
    done
    rm -rf "${EXPERIMENT_DIR}/${config}/${SUITE}"
  done

  rm -rf "${EXPERIMENT_DIR}/logs"
  find "${EXPERIMENT_DIR}" -type f \( \
      -name 'scarab_current*' -o \
      -name 'scarab' -o \
      -name 'PARAMS.in' -o \
      -name '*.warmup' -o \
      -name 'ramulator.stat.out' \
    \) -delete 2>/dev/null || true

  echo "Final layout:"
  find "${EXPERIMENT_DIR}" -mindepth 1 -maxdepth 3 -type d | sort | head -80
  echo "..."
  echo "Done. Commit-friendly tree is at: ${EXPERIMENT_DIR}"
}

run_group_sims() {
  local f name
  local -a groups
  mapfile -t groups < <(list_group_descriptors)
  if [[ ${#groups[@]} -eq 0 ]]; then
    echo "ERROR: no generated group descriptors in ${GEN_DIR}" >&2
    exit 1
  fi
  for f in "${groups[@]}"; do
    name="hpca2027/generated/$(basename "${f}" .json)"
    echo "=== Simulating ${name} ==="
    ./sci --sim "${name}"
  done
}

run_sim() {
  register_traces
  generate_descriptors
  write_experiment_gitignore
  run_group_sims
  ./sci --collect-stats "${MASTER_DESCRIPTOR}" || true
  finalize_results
}

case "${1:-}" in
  ""|--all)
    register_traces
    generate_descriptors
    write_experiment_gitignore
    ./sci --build-scarab "${MASTER_DESCRIPTOR}"
    run_group_sims
    ./sci --collect-stats "${MASTER_DESCRIPTOR}" || true
    finalize_results
    ;;
  --sim-only)
    run_sim
    ;;
  --dry-run)
    generate_descriptors
    ;;
  --status)
    ./sci --status "${MASTER_DESCRIPTOR}"
    ;;
  --collect-stats)
    ./sci --collect-stats "${MASTER_DESCRIPTOR}"
    ;;
  --finalize)
    finalize_results
    ;;
  --visualize)
    ./sci --visualize "${MASTER_DESCRIPTOR}"
    ;;
  -h|--help)
    usage
    ;;
  *)
    echo "Unknown option: $1" >&2
    usage >&2
    exit 1
    ;;
esac
