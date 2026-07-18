#!/usr/bin/env bash
# Shared helpers for hpca2027 full-trace (no warmup) launchers.
# Required vars set by caller before sourcing:
#   INFRA_DIR, HPCA_DIR, DESCRIPTOR, DESCRIPTOR_JSON, TRACES_DIR, EXPERIMENT_DIR,
#   SUITE, SUBSUITE, CONFIGS, EXCLUDE_APPS

is_excluded() {
  local app="$1" x
  for x in "${EXCLUDE_APPS[@]+"${EXCLUDE_APPS[@]}"}"; do
    [[ "${app}" == "${x}" ]] && return 0
  done
  return 1
}

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

usage_full_trace() {
  local name
  name="$(basename "$0")"
  cat <<EOF
Usage: ${name} [option]

  (default) / --sim-only
                     Register traces, refresh JSON, sim (reuse existing Scarab/docker), finalize
  --build            Same as default, but also ./sci --build-scarab first (slow; rarely needed)
  --dry-run          Print per-app SP capacity and update descriptor only
  --status           Show simulation status
  --collect-stats    Collect stats only
  --finalize         Flatten to {app}/{simpoint} and strip binaries/logs
  --visualize        Run descriptor visualization
  -h, --help         Show this help

Tip: do NOT run ./sci --build-scarab every time. A cached Scarab binary + docker image is enough
unless you changed Scarab source or the workload Dockerfile.
EOF
}

# Avoid a full docker rebuild just because scarab-infra HEAD moved (descriptor-only commits).
# Retag the newest local allbench_traces image to the current infra short hash if missing.
ensure_docker_image_reuse() {
  local githash img src
  githash="$(git -C "${INFRA_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -z "${githash}" ]]; then
    return 0
  fi
  img="allbench_traces:${githash}"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    return 0
  fi
  src="$(docker images allbench_traces --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
    | grep -v '<none>' | head -1 || true)"
  if [[ -z "${src}" ]]; then
    echo "WARN: no local allbench_traces image to retag; first sim may build docker (slow)." >&2
    return 0
  fi
  echo "Reusing docker image: ${src} -> ${img} (skip full --build-image)"
  docker tag "${src}" "${img}"
}

pre_sim_hook() { :; }
post_sim_hook() { :; }

register_and_prepare() {
  register_traces
  update_descriptor
  write_experiment_gitignore
  ensure_docker_image_reuse
}

run_sim() {
  register_and_prepare
  pre_sim_hook
  ./sci --sim "${DESCRIPTOR}"
  post_sim_hook
  ./sci --collect-stats "${DESCRIPTOR}" || true
  finalize_results
}

main_full_trace() {
  cd "${INFRA_DIR}"

  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi

  case "${1:-}" in
    ""|--all|--sim-only)
      # Default path: no --build-scarab (reuse cached binary).
      run_sim
      ;;
    --build)
      register_and_prepare
      ./sci --build-scarab "${DESCRIPTOR}"
      pre_sim_hook
      ./sci --sim "${DESCRIPTOR}"
      post_sim_hook
      ./sci --collect-stats "${DESCRIPTOR}" || true
      finalize_results
      ;;
    --dry-run)
      update_descriptor
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
      usage_full_trace
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage_full_trace >&2
      exit 1
      ;;
  esac
}

# Refresh workload list + full_warmup=0 / inst_limit=suite_max+1M on every
# configuration entry present in the descriptor JSON.
update_descriptor() {
  discover_workloads
  if [[ ${#WORKLOADS[@]} -eq 0 ]]; then
    echo "ERROR: no workloads found under ${TRACES_DIR}" >&2
    exit 1
  fi

  python3 - "${TRACES_DIR}" "${DESCRIPTOR_JSON}" "${WORKLOADS[@]}" <<'PY'
import json, sys, zipfile
from pathlib import Path

traces_dir = Path(sys.argv[1])
desc_path = Path(sys.argv[2])
workloads = sys.argv[3:]

def segment_size(app_dir: Path):
    for p in [app_dir / "fingerprint" / "segment_size", app_dir / "simpoints" / "segment_size"]:
        if p.is_file():
            try:
                return int(p.read_text().strip().split()[0])
            except Exception:
                pass
    cl = app_dir / "trace_clustering_info.json"
    if cl.is_file():
        c = json.loads(cl.read_text())
        if "segment_size" in c:
            return int(c["segment_size"])
    return None

def sp_insts(app_dir: Path):
    rel = app_dir / "RELEASE_MANIFEST.json"
    if rel.is_file():
        out = []
        for sp in json.loads(rel.read_text()).get("simpoints", []):
            out.append(int(sp["captured_fetched_instructions"]))
        if out:
            return out
    seg = segment_size(app_dir)
    zdir = app_dir / "traces_simp" / "trace"
    out = []
    for z in sorted(zdir.glob("*.zip"), key=lambda p: (0, int(p.stem)) if p.stem.isdigit() else (1, p.stem)):
        if not z.stem.isdigit():
            continue
        with zipfile.ZipFile(z) as zf:
            chunks = sum(1 for n in zf.namelist() if Path(n).name.startswith("chunk."))
        if seg is None:
            raise SystemExit(f"{app_dir.name}: missing segment_size and RELEASE_MANIFEST")
        out.append(chunks * seg)
    return out

rows = []
suite_max = 0
for app in workloads:
    vals = sp_insts(traces_dir / app)
    if not vals:
        print(f"WARN: {app}: no simpoints, skipping", file=sys.stderr)
        continue
    rows.append((app, len(vals), min(vals), max(vals), sum(vals)))
    suite_max = max(suite_max, max(vals))

if not rows:
    raise SystemExit("no apps with simpoints")

inst_limit = suite_max + 1_000_000

print(f"{'app':16} {'n_sp':>4} {'min/SP':>12} {'max/SP':>12} {'app TOTAL':>14}")
print("-" * 64)
for app, n, mn, mx, tot in rows:
    print(f"{app:16} {n:4} {mn:12,} {mx:12,} {tot:14,}")
print(f"\nfull_warmup=0  inst_limit={inst_limit:,}  (suite max SP={suite_max:,} + 1M pad)")

desc = json.loads(desc_path.read_text())
desc["simulations"][0]["workload"] = [r[0] for r in rows]
desc["_comment"] = (
    f"No Scarab warmup; inst_limit={inst_limit} (>= max SP {suite_max}). "
    "Each simpoint runs to EOF (full available instructions)."
)

common = f"--icache_size 32768 --inst_limit {inst_limit} --full_warmup 0"
runtime_ifuse_knobs = (
    "--ifuse_fusion_distance 512 --ifuse_apt_match_policy 0 "
    "--ifuse_runtime_training_enabled 1 --ifuse_fct_hash_bits 10"
)
for name, cfg in desc.get("configurations", {}).items():
    if name == "runtime_ifuse":
        cfg["params"] = (
            f"{common} {runtime_ifuse_knobs} "
            f"--ifuse_training_insert_threshold 1000"
        )
    elif name.startswith("train_thresh_"):
        # Sweep configs: train_thresh_10 / _100 / _1000 / _10000
        try:
            thresh = int(name[len("train_thresh_"):])
        except ValueError:
            raise SystemExit(f"bad train_thresh config name: {name}")
        cfg["params"] = (
            f"{common} {runtime_ifuse_knobs} "
            f"--ifuse_training_insert_threshold {thresh}"
        )
    elif name == "baseline":
        cfg["params"] = (
            f"{common} --ifuse_fusion_distance 0 --ifuse_runtime_training_enabled 0"
        )
    elif name == "pass1":
        cfg["params"] = (
            f"{common} --ifuse_fusion_distance 0 --ideal_fusion_pass 1 "
            f"--ideal_fusion_log /dev/shm/baseline/ideal_fusion_candidates/{{workload}}/{{cluster_id}}.csv"
        )
    elif name == "pass2":
        cfg["params"] = (
            f"{common} --ifuse_fusion_distance 0 --ideal_fusion_pass 2 "
            f"--ideal_fusion_log /dev/shm/baseline/ideal_fusion_candidates/{{workload}}/{{cluster_id}}.csv"
        )
    else:
        # Preserve unknown configs but force window.
        prev = cfg.get("params", "")
        # Strip old inst_limit / full_warmup if present, then prepend common.
        cfg["params"] = f"{common} {prev}".strip()

desc_path.write_text(json.dumps(desc, indent=2) + "\n")
print(f"Updated {desc_path}")
print(f"Configs: {list(desc.get('configurations', {}))}")
PY
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
tmp/
scarab_stage/
**/scarab_current*
**/scarab_*
**/*.warmup
**/ramulator.stat.out
**/PARAMS.in
**/collected_stats.csv
EOF
}

# Move app dirs into EXPERIMENT_DIR/{app}/{simpoint}/ from any of:
#   {config}/datacenter/datacenter/{app}
#   {config}/{app}
# so the commit tree is just apps under the experiment root (no config / suite / logs).
_move_app_to_experiment_root() {
  local app_dir="$1"
  local app dest
  app="$(basename "${app_dir}")"
  dest="${EXPERIMENT_DIR}/${app}"
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
}

# Flatten suite/subsuite under each config to {config}/{app}/{simpoint}/.
# Used for multi-config sweeps where configs must not share an app root.
_flatten_config_keep_nesting() {
  local config="$1"
  local nested="${EXPERIMENT_DIR}/${config}/${SUITE}/${SUBSUITE}"
  local flat_config="${EXPERIMENT_DIR}/${config}"

  if [[ -d "${nested}" ]]; then
    find "${nested}" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' app_dir; do
      local app dest
      app="$(basename "${app_dir}")"
      dest="${flat_config}/${app}"
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
    rm -rf "${flat_config}/${SUITE}"
  fi
}

finalize_results() {
  if [[ ! -d "${EXPERIMENT_DIR}" ]]; then
    echo "ERROR: experiment dir not found: ${EXPERIMENT_DIR}" >&2
    exit 1
  fi

  # KEEP_CONFIG_NESTING=1 -> {config}/{app}/{sp}/ (multi-config sweeps)
  # default         -> {app}/{sp}/               (single-config experiments)
  if [[ "${KEEP_CONFIG_NESTING:-0}" == "1" ]]; then
    echo "Finalizing ${EXPERIMENT_DIR} -> {config}/{app}/{simpoint}/ ..."
  else
    echo "Finalizing ${EXPERIMENT_DIR} -> {app}/{simpoint}/ (no config/suite/logs)..."
  fi
  write_experiment_gitignore

  for config in "${CONFIGS[@]}"; do
    if [[ "${KEEP_CONFIG_NESTING:-0}" == "1" ]]; then
      _flatten_config_keep_nesting "${config}"
      continue
    fi

    local nested="${EXPERIMENT_DIR}/${config}/${SUITE}/${SUBSUITE}"
    local flat_config="${EXPERIMENT_DIR}/${config}"

    if [[ -d "${nested}" ]]; then
      find "${nested}" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' app_dir; do
        _move_app_to_experiment_root "${app_dir}"
      done
      rm -rf "${EXPERIMENT_DIR}/${config}"
    elif [[ -d "${flat_config}" ]]; then
      # Already flattened once to {config}/{app}; lift apps to experiment root.
      find "${flat_config}" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' app_dir; do
        # Skip leftover suite dirs if any.
        local name
        name="$(basename "${app_dir}")"
        if [[ "${name}" == "${SUITE}" || "${name}" == "${SUBSUITE}" ]]; then
          continue
        fi
        _move_app_to_experiment_root "${app_dir}"
      done
      rm -rf "${flat_config}"
    fi
  done

  # Drop infra noise at experiment root and under apps.
  rm -rf "${EXPERIMENT_DIR}/logs" "${EXPERIMENT_DIR}/tmp" "${EXPERIMENT_DIR}/scarab_stage"
  find "${EXPERIMENT_DIR}" -type f \( \
      -name 'scarab_current*' -o \
      -name 'scarab' -o \
      -name 'PARAMS.in' -o \
      -name '*.warmup' -o \
      -name 'ramulator.stat.out' -o \
      -name 'job_*.out' -o \
      -name 'job_*.err' \
    \) -delete 2>/dev/null || true

  if [[ "${KEEP_CONFIG_NESTING:-0}" == "1" ]]; then
    echo "Final layout (configs kept):"
    find "${EXPERIMENT_DIR}" -mindepth 1 -maxdepth 3 \( -type d -o -type f \) | sort | head -80
    echo "..."
    echo "Done. Commit-friendly tree: ${EXPERIMENT_DIR}/{config}/{app}/{simpoint}/"
  else
    echo "Final layout (apps at experiment root):"
    find "${EXPERIMENT_DIR}" -mindepth 1 -maxdepth 2 \( -type d -o -type f \) | sort | head -80
    echo "..."
    echo "Done. Commit-friendly tree: ${EXPERIMENT_DIR}/{app}/{simpoint}/"
  fi
}
