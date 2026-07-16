#!/usr/bin/env bash
# Runtime I-Fuse: 20M warmup, then measure until end of each simpoint zip
# =============================================================================
# Descriptor : runtime_ifuse_20m_warmup_rest_of_trace.json
# Experiment : simulations/runtime-ifuse/
# Layout     : simulations/runtime-ifuse/{baseline|runtime_ifuse}/{app}/{simpoint}/
#              (suite/subsuite nesting is removed after the run for easy commits)
#
# Window     : --full_warmup 20000000 --inst_limit 200000000
#              (200M is a ceiling; Scarab stops at EOF if the zip is shorter)
# Workloads  : all apps under /dev/shm/baseline/simpoint_traces (except suite dirs)
#
# Approximate measured windows after 20M warmup:
#   sssp_ego_fb              ~10M
#   appworld/4, core_bench/4 ~30M
#   most 60M zips            ~40M
#   bfs/dfs/pagerank         ~70M
#
# Usage:
#   ./runtime_ifuse_20m_warmup_rest_of_trace.sh              # register, build, sim, finalize
#   ./runtime_ifuse_20m_warmup_rest_of_trace.sh --sim-only   # register + sim + finalize
#   ./runtime_ifuse_20m_warmup_rest_of_trace.sh --status
#   ./runtime_ifuse_20m_warmup_rest_of_trace.sh --collect-stats
#   ./runtime_ifuse_20m_warmup_rest_of_trace.sh --finalize   # flatten layout + strip junk
#   ./runtime_ifuse_20m_warmup_rest_of_trace.sh --visualize
#
# Finalize removes job logs, scarab binaries, and the datacenter/datacenter/
# nesting so the tree is commit-friendly under src/simulations/runtime-ifuse/.

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESCRIPTOR="hpca2027/runtime_ifuse_20m_warmup_rest_of_trace"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
EXPERIMENT_DIR="/users/deepmish/scarab/src/simulations/runtime-ifuse"
SUITE="datacenter"
SUBSUITE="datacenter"
CONFIGS=(baseline runtime_ifuse)

# Every app directory under traces_dir except the suite symlink tree.
mapfile -t WORKLOADS < <(
  find "${TRACES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name "${SUITE}" -printf '%f\n' | sort
)

cd "${INFRA_DIR}"

if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "${HOME}/miniconda3/etc/profile.d/conda.sh"
  conda activate scarabinfra 2>/dev/null || true
fi

usage() {
  cat <<'EOF'
Usage: runtime_ifuse_20m_warmup_rest_of_trace.sh [option]

  (default)          Register traces, build Scarab, simulate, collect stats, finalize
  --sim-only         Register traces, simulate, collect stats, finalize (skip build)
  --status           Show simulation status (before finalize; nested layout)
  --collect-stats    Collect stats only
  --finalize         Flatten to {config}/{app}/{simpoint} and strip binaries/logs
  --visualize        Run descriptor visualization
  -h, --help         Show this help
EOF
}

register_traces() {
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

# Keep: PARAMS.out, *.stat.*, sim.log, collected_stats.csv, plots
EOF
}

# Move simulations/runtime-ifuse/{config}/datacenter/datacenter/{app}/{cid}
#   -> simulations/runtime-ifuse/{config}/{app}/{cid}
# and drop job logs / scarab binaries.
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
      # Already flat, or sim not run for this config yet.
      continue
    fi
    mkdir -p "${EXPERIMENT_DIR}/${config}"
    # Move each app directory up two levels.
    find "${nested}" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' app_dir; do
      local app
      app="$(basename "${app_dir}")"
      local dest="${EXPERIMENT_DIR}/${config}/${app}"
      if [[ -e "${dest}" ]]; then
        echo "  merging ${app_dir} -> ${dest}"
        mkdir -p "${dest}"
        # Move simpoint dirs; prefer newer nested copy if both exist.
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
    # Remove empty suite/subsuite scaffolding.
    rm -rf "${EXPERIMENT_DIR}/${config}/${SUITE}"
  done

  # Strip binaries and job logs from the experiment tree.
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

run_sim() {
  register_traces
  write_experiment_gitignore
  ./sci --sim "${DESCRIPTOR}"
  ./sci --collect-stats "${DESCRIPTOR}" || true
  finalize_results
}

case "${1:-}" in
  ""|--all)
    register_traces
    write_experiment_gitignore
    ./sci --build-scarab "${DESCRIPTOR}"
    ./sci --sim "${DESCRIPTOR}"
    ./sci --collect-stats "${DESCRIPTOR}" || true
    finalize_results
    ;;
  --sim-only)
    run_sim
    ;;
  --status)
    ./sci --status "${DESCRIPTOR}"
    ;;
  --collect-stats)
    ./sci --collect-stats "${DESCRIPTOR}"
    ;;
  --finalize)
    finalize_results
    ;;
  --visualize)
    ./sci --visualize "${DESCRIPTOR}"
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
