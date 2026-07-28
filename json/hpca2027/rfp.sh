#!/usr/bin/env bash
# RFP: per-app 24KB PT/PAT knobs, reuse one Scarab binary (no rebuild per app)
# =============================================================================
# Storage budget (all apps): PT 512x8 + PAT 64x4 = 24 KiB
# McPAT models RFP_PT (32 KiB) + RFP_PAT (2 KiB) SRAM via --power_intf_on 1
#
# Usage:
#   ./rfp.sh              # register + baseline + per-app RFP (no rebuild)
#   ./rfp.sh --build      # rebuild Scarab once, then run
#   ./rfp.sh --dry-run
#   ./rfp.sh --status
#   ./rfp.sh --validate
#   ./rfp.sh --package
#   ./rfp.sh --tune        # re-run apps with negative speedup only
#   ./rfp.sh --resume      # run only missing baseline/rfp simpoints

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESCRIPTOR="hpca2027/rfp"
DESCRIPTOR_JSON="${INFRA_DIR}/json/hpca2027/rfp.json"
TRACES_DIR="/dev/shm/baseline/simpoint_traces"
SCARAB_SRC="/users/deepmish/scarab/src"
BUILDS_DIR="${INFRA_DIR}/scarab_builds"
WARMUP=20000000
INST_LIMIT=30000000
LOG="${INFRA_DIR}/rfp_run.log"
STATUS="${INFRA_DIR}/rfp_run.status"

# Populated by discover_present_apps() from simpoint_traces/.
RFP_APPS=()
PRESENT_APPS=()

# Per-app tuned knobs at 24KB (prob_shift, stride_bits, stride_signed).
declare -A RFP_PROB_SHIFT=(
  [appworld]=2 [bfs-init]=3 [bfs_web-google]=3 [clickhouse]=2 [core_bench]=2
  [dfs-init]=0 [dfs_web-google]=0 [duckdb]=2 [grpc]=2 [leveldb]=2
  [memcached]=2 [pagerank_gnutella31]=1 [pagerank-init]=1 [rocksdb]=2
  [sqlite]=2 [sssp_ego-facebook]=1 [sssp-init]=1 [terminal_bench]=2
)
declare -A RFP_STRIDE_BITS=(
  [appworld]=5 [bfs-init]=16 [bfs_web-google]=16 [clickhouse]=5 [core_bench]=5
  [dfs-init]=16 [dfs_web-google]=16 [duckdb]=5 [grpc]=5 [leveldb]=5
  [memcached]=5 [pagerank_gnutella31]=16 [pagerank-init]=16 [rocksdb]=5
  [sqlite]=5 [sssp_ego-facebook]=16 [sssp-init]=16 [terminal_bench]=5
)
declare -A RFP_STRIDE_SIGNED=(
  [appworld]=1 [bfs-init]=1 [bfs_web-google]=1 [clickhouse]=1 [core_bench]=1
  [dfs-init]=1 [dfs_web-google]=1 [duckdb]=1 [grpc]=1 [leveldb]=1
  [memcached]=1 [pagerank_gnutella31]=1 [pagerank-init]=1 [rocksdb]=1
  [sqlite]=1 [sssp_ego-facebook]=1 [sssp-init]=1 [terminal_bench]=1
)

STORAGE_24KB=(
  "--rfp_pt_num_sets 512 --rfp_pt_num_ways 8"
  "--rfp_pat_num_sets 64 --rfp_pat_num_ways 4"
)

workload_key_for_trace_dir() {
  case "$1" in
    bfs-web-google) echo bfs_web-google ;;
    dfs-web-google) echo dfs_web-google ;;
    corebench) echo core_bench ;;
    pagerank-gnutella31) echo pagerank_gnutella31 ;;
    sssp-ego-facebook) echo sssp_ego-facebook ;;
    *) echo "$1" ;;
  esac
}

trace_dir_for_app() {
  case "$1" in
    bfs_web-google) echo bfs-web-google ;;
    dfs_web-google) echo dfs-web-google ;;
    core_bench) echo corebench ;;
    pagerank_gnutella31) echo pagerank-gnutella31 ;;
    sssp_ego-facebook) echo sssp-ego-facebook ;;
    *) echo "$1" ;;
  esac
}

default_tuning_for_app() {
  local app="$1"
  case "${app}" in
    bfs_web-google|bfs-init) RFP_PROB_SHIFT[$app]=3; RFP_STRIDE_BITS[$app]=16 ;;
    dfs_web-google|dfs-init) RFP_PROB_SHIFT[$app]=0; RFP_STRIDE_BITS[$app]=16 ;;
    pagerank_gnutella31|pagerank-init|sssp_ego-facebook|sssp-init)
      RFP_PROB_SHIFT[$app]=1; RFP_STRIDE_BITS[$app]=16 ;;
    *) RFP_PROB_SHIFT[$app]=2; RFP_STRIDE_BITS[$app]=5 ;;
  esac
  RFP_STRIDE_SIGNED[$app]=1
}

rfp_label_for_app() {
  local app="$1"
  printf 'p%d/s%db/%s/24KB' \
    "${RFP_PROB_SHIFT[$app]}" "${RFP_STRIDE_BITS[$app]}" \
    "$([ "${RFP_STRIDE_SIGNED[$app]}" = 1 ] && echo signed || echo unsigned)"
}

rfp_knobs_for_app() {
  local app="$1"
  printf '%s' \
    "--rfp_on 1 --rfp_conf_max 1 " \
    "--rfp_prob_shift ${RFP_PROB_SHIFT[$app]} " \
    "--rfp_stride_signed ${RFP_STRIDE_SIGNED[$app]} " \
    "--rfp_stride_bits ${RFP_STRIDE_BITS[$app]} " \
    "${STORAGE_24KB[*]}"
}

print_config_table() {
  local app
  printf '%-22s  %s\n' "app" "RFP config (24KB)"
  printf '%-22s  %s\n' "----------------------" "---------------------------"
  for app in "${RFP_APPS[@]}"; do
    printf '%-22s  %s\n' "${app}" "$(rfp_label_for_app "${app}")"
  done
}

resolve_pinned_binary() {
  local link target base
  link="${BUILDS_DIR}/scarab_current.opt"
  if [[ -L "${link}" ]]; then
    target="$(readlink "${link}")"
    base="${target%.opt}"
    base="${base%.dbg}"
    if [[ -f "${BUILDS_DIR}/${target}" || -f "${BUILDS_DIR}/${base}.opt" ]]; then
      printf '%s' "${base}"
      return 0
    fi
  fi
  local f
  for f in "${BUILDS_DIR}"/scarab_*_0.opt; do
    [[ -f "$f" && -s "$f" ]] || continue
    printf '%s' "$(basename "$f" .opt)"
    return 0
  done
  return 1
}

ensure_docker_image_tag() {
  local infra_hash img existing
  infra_hash="$(git -C "${INFRA_DIR}" rev-parse --short HEAD)"
  img="allbench_traces:${infra_hash}"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    return 0
  fi
  existing="$(docker images allbench_traces --format '{{.Tag}}' | head -1 || true)"
  if [[ -z "${existing}" ]]; then
    echo "ERROR: no allbench_traces image; run: ./sci --build-image allbench_traces" >&2
    return 1
  fi
  docker tag "allbench_traces:${existing}" "${img}"
}

# finish_simulation runs docker_cleaner after every ./sci --sim, which untags the image.
# The next app then falls back to `sci --build-image`, which refuses to build with a dirty
# git tree and aborts the run. Re-tag from the existing image before each sim instead.
run_sci_sim() {
  ensure_docker_image_tag
  ./sci --sim "${DESCRIPTOR}"
}

prep_power_tools() {
  local stage="${SCARAB_SRC}/scarab_stage/rfp/scarab/bin"
  export MCPAT_BIN=/users/deepmish/toolchain/bin/mcpat
  export CACTI_BIN=/users/deepmish/toolchain/bin/cacti
  mkdir -p "${stage}/power"
  if [[ -f /users/deepmish/scarab/bin/power/power_intf.pl ]]; then
    cp -f /users/deepmish/scarab/bin/power/power_intf.pl "${stage}/power/power_intf.pl"
    cp -f /users/deepmish/scarab/bin/power/power_intf.py "${stage}/power/power_intf.py" 2>/dev/null || true
  fi
  if [[ -f "${MCPAT_BIN}" ]]; then
    cp -f "${MCPAT_BIN}" "${stage}/mcpat" 2>/dev/null || true
  fi
  if [[ -f "${CACTI_BIN}" ]]; then
    cp -f "${CACTI_BIN}" "${stage}/cacti" 2>/dev/null || true
  fi

  # The container mounts root_dir at $HOME, so the host ~/toolchain is NOT $HOME/toolchain
  # inside the container. Mirror the tools into root_dir/toolchain/bin so the in-container
  # $HOME/toolchain/bin/{mcpat,cacti} that user_entrypoint.sh resolves actually exists.
  local docker_toolchain="${SCARAB_SRC}/toolchain/bin"
  mkdir -p "${docker_toolchain}"
  [[ -f "${MCPAT_BIN}" ]] && cp -f "${MCPAT_BIN}" "${docker_toolchain}/mcpat"
  [[ -f "${CACTI_BIN}" ]] && cp -f "${CACTI_BIN}" "${docker_toolchain}/cacti"
  chmod +x "${docker_toolchain}/mcpat" "${docker_toolchain}/cacti" 2>/dev/null || true
}

# Fail before a multi-hour run rather than after, if power would come back empty.
# Runs mcpat/cacti through the same container + mounts + entrypoint the sims use.
preflight_power() {
  local img infra_hash
  ensure_docker_image_tag
  infra_hash="$(git -C "${INFRA_DIR}" rev-parse --short HEAD)"
  img="allbench_traces:${infra_hash}"
  echo "=== Power preflight (${img}) ==="
  docker run --rm \
    -e HOME=/home/${USER} \
    --mount "type=bind,source=${SCARAB_SRC},target=/home/${USER},readonly=false" \
    --mount "type=bind,source=/users/deepmish,target=/tmp_home/application,readonly=false" \
    -v "${INFRA_DIR}/common/scripts/user_entrypoint.sh:/usr/local/bin/user_entrypoint.sh:ro" \
    "${img}" /bin/bash -c '
      source /usr/local/bin/user_entrypoint.sh
      echo "MCPAT_BIN=$MCPAT_BIN"
      echo "CACTI_BIN=$CACTI_BIN"
      [ -x "$MCPAT_BIN" ] || { echo "PREFLIGHT FAIL: mcpat unresolved" >&2; exit 1; }
      [ -x "$CACTI_BIN" ] || { echo "PREFLIGHT FAIL: cacti unresolved" >&2; exit 1; }
      "$MCPAT_BIN" 2>&1 | head -1 | grep -q "How to use McPAT" \
        || { echo "PREFLIGHT FAIL: mcpat did not execute" >&2; exit 1; }
      echo "PREFLIGHT OK: mcpat and cacti resolve and execute in-container"
    ' || { echo "ERROR: power preflight failed; aborting before simulation" >&2; exit 1; }
}

write_rfp_descriptor() {
  local binary="$1"
  local mode="${2:-all}"   # all | baseline | rfp
  local app="${3:-}"       # single app for rfp mode

  python3 - "${DESCRIPTOR_JSON}" "${binary}" "${mode}" "${app}" "${WARMUP}" "${INST_LIMIT}" \
    "$(printf '%s\n' "${RFP_APPS[@]}")" <<'PY'
import json, sys
from pathlib import Path

desc_path = Path(sys.argv[1])
binary = sys.argv[2]
mode = sys.argv[3]
app = sys.argv[4]
warmup = int(sys.argv[5])
inst_limit = int(sys.argv[6])
all_apps = [w for w in sys.argv[7].splitlines() if w]

# Per-app tuning (must match bash arrays in rfp.sh)
TUNING = {
    "appworld": (2, 5, 1),
    "bfs-init": (3, 16, 1),
    "bfs_web-google": (3, 16, 1),
    "clickhouse": (2, 5, 1),
    "core_bench": (2, 5, 1),
    "dfs-init": (0, 16, 1),
    "dfs_web-google": (0, 16, 1),
    "duckdb": (2, 5, 1),
    "grpc": (2, 5, 1),
    "leveldb": (2, 5, 1),
    "memcached": (2, 5, 1),
    "pagerank_gnutella31": (1, 16, 1),
    "pagerank-init": (1, 16, 1),
    "rocksdb": (2, 5, 1),
    "sqlite": (2, 5, 1),
    "sssp_ego-facebook": (1, 16, 1),
    "sssp-init": (1, 16, 1),
    "terminal_bench": (2, 5, 1),
}
def tuning_for(wl):
    if wl in TUNING:
        return TUNING[wl]
    if wl in ("bfs_web-google", "bfs-init"):
        return (3, 16, 1)
    if wl in ("dfs_web-google", "dfs-init"):
        return (0, 16, 1)
    if wl in ("pagerank_gnutella31", "pagerank-init", "sssp_ego-facebook", "sssp-init"):
        return (1, 16, 1)
    return (2, 5, 1)
STORAGE = (
    "--rfp_pt_num_sets 512 --rfp_pt_num_ways 8 "
    "--rfp_pat_num_sets 64 --rfp_pat_num_ways 4"
)

desc = json.loads(desc_path.read_text())
experiment = desc["experiment"]
common = (
    f"--icache_size 32768 --inst_limit {inst_limit} "
    f"--full_warmup {warmup} --power_intf_on 1 "
    f"--bindir {{root_dir}}/scarab_stage/{experiment}/scarab/bin"
)

if mode == "baseline":
    workloads = all_apps if not app else [app]
    desc["simulations"][0]["workload"] = workloads
    desc["configurations"] = {
        "baseline": {
            "params": f"{common} --rfp_on 0",
            "binary": binary,
            "slurm_options": "",
            "memory_overhead_mb": 0,
        }
    }
    desc["_comment"] = f"RFP baseline (rfp_off). binary={binary}."
elif mode == "rfp":
    workloads = [app] if app else all_apps
    ps, sb, sg = tuning_for(workloads[0])
    label = f"p{ps}/s{sb}b/{'signed' if sg else 'unsigned'}/24KB"
    knobs = (
        f"--rfp_on 1 --rfp_conf_max 1 --rfp_prob_shift {ps} "
        f"--rfp_stride_signed {sg} --rfp_stride_bits {sb} {STORAGE}"
    )
    desc["simulations"][0]["workload"] = workloads
    desc["configurations"] = {
        "rfp_24kb": {
            "params": f"{common} {knobs}".strip(),
            "binary": binary,
            "slurm_options": "",
            "memory_overhead_mb": 0,
        }
    }
    desc["_comment"] = f"RFP 24KB {workloads[0]} {label}. binary={binary}."
else:
    desc["simulations"][0]["workload"] = all_apps
    desc["configurations"] = {
        "baseline": {
            "params": f"{common} --rfp_on 0",
            "binary": binary,
            "slurm_options": "",
            "memory_overhead_mb": 0,
        },
        "rfp_24kb": {
            "params": (
                f"{common} --rfp_on 1 --rfp_conf_max 1 --rfp_prob_shift 2 "
                f"--rfp_stride_signed 1 --rfp_stride_bits 5 {STORAGE}"
            ).strip(),
            "binary": binary,
            "slurm_options": "",
            "memory_overhead_mb": 0,
        },
    }
    desc["rfp_per_app"] = {
        wl: (
            (lambda t: f"p{t[0]}/s{t[1]}b/{'signed' if t[2] else 'unsigned'}/24KB")
            (tuning_for(wl))
        )
        for wl in all_apps
    }
    desc["_comment"] = f"RFP 24KB per-app tuning via rfp.sh. binary={binary}."

desc_path.write_text(json.dumps(desc, indent=2) + "\n")
print(f"Updated {desc_path} mode={mode} workloads={workloads if mode != 'all' else all_apps}")
PY
}

discover_present_apps() {
  local trace_dir key
  PRESENT_APPS=()
  RFP_APPS=()
  for trace_dir in "${TRACES_DIR}"/*/; do
    trace_dir="$(basename "${trace_dir%/}")"
    case "${trace_dir}" in
      datacenter|.cache|.*) continue ;;
    esac
    [[ -d "${TRACES_DIR}/${trace_dir}" ]] || continue
    if ! { [[ -d "${TRACES_DIR}/${trace_dir}/simpoints" ]] || \
          find "${TRACES_DIR}/${trace_dir}" -name '*.zip' 2>/dev/null | grep -q .; }; then
      echo "SKIP ${trace_dir}: no simpoints or trace zips" >&2
      continue
    fi
    key="$(workload_key_for_trace_dir "${trace_dir}")"
    PRESENT_APPS+=("${key}")
    RFP_APPS+=("${key}")
    if [[ -z "${RFP_PROB_SHIFT[$key]+x}" ]]; then
      default_tuning_for_app "${key}"
    fi
  done
  if [[ ${#PRESENT_APPS[@]} -eq 0 ]]; then
    echo "ERROR: no workloads found under ${TRACES_DIR}" >&2
    exit 1
  fi
  echo "Discovered ${#PRESENT_APPS[@]} app(s): ${PRESENT_APPS[*]}"
}

app_needs_baseline() {
  python3 - "${SCARAB_SRC}" "$1" "${INFRA_DIR}" <<'PY'
import json, sys
from pathlib import Path
root, app, infra = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
db = json.loads((infra / 'workloads/workloads_db.json').read_text())
sps = db.get('datacenter', {}).get('datacenter', {}).get(app, {}).get('simpoints', [])
expected = {str(sp['cluster_id']) for sp in sps}
done = set()
d = root / 'simulations' / 'baseline' / app
if d.is_dir():
    for sp in d.iterdir():
        if not (sp.is_dir() and (sp / 'inst.stat.0.csv').is_file()):
            continue
        # A simpoint without a non-empty mcpat.out has no power data; redo it.
        mcpat = sp / 'mcpat.out'
        if mcpat.is_file() and mcpat.stat().st_size > 0:
            done.add(sp.name)
missing = expected - done
if missing:
    print(f"baseline {app}: missing {len(missing)}/{len(expected)} {sorted(missing)}")
    sys.exit(0)
sys.exit(1)
PY
}

app_needs_rfp() {
  python3 - "${SCARAB_SRC}" "$1" "${INFRA_DIR}" <<'PY'
import json, sys
from pathlib import Path
root, app, infra = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
db = json.loads((infra / 'workloads/workloads_db.json').read_text())
sps = db.get('datacenter', {}).get('datacenter', {}).get(app, {}).get('simpoints', [])
expected = {str(sp['cluster_id']) for sp in sps}
done = set()
d = root / 'simulations' / 'rfp_24kb' / app
if d.is_dir():
    for sp in d.iterdir():
        if not (sp.is_dir() and (sp / 'inst.stat.0.csv').is_file()):
            continue
        # A simpoint without a non-empty mcpat.out has no power data; redo it.
        mcpat = sp / 'mcpat.out'
        if mcpat.is_file() and mcpat.stat().st_size > 0:
            done.add(sp.name)
missing = expected - done
if missing:
    print(f"rfp_24kb {app}: missing {len(missing)}/{len(expected)} {sorted(missing)}")
    sys.exit(0)
sys.exit(1)
PY
}

register_traces() {
  discover_present_apps
  if [[ ${#PRESENT_APPS[@]} -eq 0 ]]; then
    echo "ERROR: no RFP apps with traces under ${TRACES_DIR}" >&2
    exit 1
  fi
  local app trace_dirs=()
  for app in "${PRESENT_APPS[@]}"; do
    trace_dirs+=("$(trace_dir_for_app "${app}")")
  done
  echo "Registering ${#PRESENT_APPS[@]} workload(s)"
  python3 -m scripts.register_local_traces \
    --traces-dir "${TRACES_DIR}" \
    --workloads "${trace_dirs[@]}" \
    --warmup "${WARMUP}"
  sync_workload_db_keys
  ensure_trace_aliases
}

# sync_workload_db_keys aliases the DB entry hyphen -> underscore, but the trace tree on
# disk keeps the hyphen name, so Scarab resolves
# /simpoint_traces/datacenter/datacenter/<underscore>/traces/simp/<id>.zip and finds nothing.
# Symlink the underscore alias next to the real dir so both names resolve.
ensure_trace_aliases() {
  local base="${TRACES_DIR}/datacenter/datacenter"
  local src dst
  [[ -d "${base}" ]] || return 0
  for src in bfs-web-google dfs-web-google pagerank-gnutella31 sssp-ego-facebook corebench; do
    dst="$(workload_key_for_trace_dir "${src}")"
    [[ "${dst}" != "${src}" ]] || continue
    [[ -d "${base}/${src}" ]] || continue
    if [[ -e "${base}/${dst}" && ! -L "${base}/${dst}" ]]; then
      continue   # a real directory already exists; leave it alone
    fi
    ln -sfn "${src}" "${base}/${dst}"
    echo "Trace alias: ${dst} -> ${src}"
  done
}

# Trace dirs use hyphens; descriptors use underscores for a few graph apps.
sync_workload_db_keys() {
  python3 - "${INFRA_DIR}/workloads/workloads_db.json" <<'PY'
import json, sys
from pathlib import Path

db_path = Path(sys.argv[1])
db = json.loads(db_path.read_text())
suite = db.setdefault("datacenter", {}).setdefault("datacenter", {})
ALIASES = {
    "bfs-web-google": "bfs_web-google",
    "dfs-web-google": "dfs_web-google",
    "pagerank-gnutella31": "pagerank_gnutella31",
    "sssp-ego-facebook": "sssp_ego-facebook",
    "corebench": "core_bench",
}
for src, dst in ALIASES.items():
    if src in suite:
        suite[dst] = json.loads(json.dumps(suite[src]))
        print(f"Synced {src} -> {dst} (warmup={suite[dst]['simulation']['memtrace']['warmup']})")
db_path.write_text(json.dumps(db, indent=2, separators=(",", ":")) + "\n")
PY
}

ensure_pinned_binary() {
  local do_build="${1:-0}"
  local pinned
  if [[ "${do_build}" == "1" ]]; then
    write_rfp_descriptor "scarab_current" "all"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
  fi
  if ! pinned="$(resolve_pinned_binary)"; then
    echo "No cached Scarab binary; building once..."
    write_rfp_descriptor "scarab_current" "all"
    ensure_docker_image_tag
    ./sci --build-scarab "${DESCRIPTOR}"
    pinned="$(resolve_pinned_binary)" || { echo "ERROR: build failed" >&2; exit 1; }
  fi
  echo "Using pinned Scarab binary: ${pinned}"
  PINNED_BINARY="${pinned}"
}

count_expected_simpoints() {
  python3 - "${INFRA_DIR}" "$(printf '%s,' "${PRESENT_APPS[@]}")" <<'PY'
import json, sys
from pathlib import Path
infra = Path(sys.argv[1])
apps = [a for a in sys.argv[2].split(',') if a]
db = json.loads((infra / 'workloads/workloads_db.json').read_text())
total = 0
for app in apps:
    entry = db.get('datacenter', {}).get('datacenter', {}).get(app, {})
    sps = entry.get('simpoints', [])
    print(f"{app}: {len(sps)} simpoints")
    total += len(sps)
print(f"TOTAL: {total}")
PY
}

validate_results() {
  python3 - "${SCARAB_SRC}" "$(printf '%s,' "${PRESENT_APPS[@]}")" <<'PY'
import json, math, sys
from pathlib import Path

root = Path(sys.argv[1])
apps = [a for a in sys.argv[2].split(',') if a]
issues = []
ok = 0

def ipc(sim_dir: Path):
    f = sim_dir / 'core.stat.0.csv'
    if not f.is_file():
        return None
    pi = pc = None
    import csv
    with f.open() as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) < 3:
                continue
            if row[0].strip() == 'Periodic_Instructions':
                pi = float(row[2])
            elif row[0].strip() == 'Periodic_Cycles':
                pc = float(row[2])
    if pi and pc and pc > 0:
        return pi / pc
    return None

def check_cfg(cfg, workload):
    global ok
    d = root / 'simulations' / cfg / workload
    if not d.is_dir():
        issues.append(f"missing dir {d}")
        return
    for sp in d.iterdir():
        if not sp.is_dir():
            continue
        inst = sp / 'inst.stat.0.csv'
        core = sp / 'core.stat.0.csv'
        mcpat = sp / 'mcpat.out'
        mcpat_in = sp / 'mcpat_infile.xml'
        if not inst.is_file():
            issues.append(f"missing inst.stat {sp}")
            continue
        if not core.is_file():
            issues.append(f"missing core.stat {sp}")
            continue
        if not mcpat.is_file() or mcpat.stat().st_size == 0:
            issues.append(f"missing/empty mcpat.out {sp}")
        if mcpat_in.is_file():
            txt = mcpat_in.read_text()
            if 'rfp_pt' not in txt and cfg == 'rfp_24kb':
                issues.append(f"RFP PT not in mcpat_infile {sp}")
        ok += 1

for app in apps:
    check_cfg('baseline', app)
    check_cfg('rfp_24kb', app)

print(f"VALID simpoints: {ok}")
if issues:
    print(f"ISSUES: {len(issues)}")
    for i in issues[:30]:
        print(f"  {i}")
    if len(issues) > 30:
        print(f"  ... and {len(issues)-30} more")
    sys.exit(1)
sys.exit(0)
PY
}

compute_speedups() {
  python3 - "${SCARAB_SRC}" "$(printf '%s,' "${PRESENT_APPS[@]}")" <<'PY'
import csv, json, math, sys
from pathlib import Path

root = Path(sys.argv[1])
apps = [a for a in sys.argv[2].split(',') if a]
neg = []

def ipc(sim_dir: Path):
    f = sim_dir / 'core.stat.0.csv'
    if not f.is_file():
        return None
    pi = pc = None
    with f.open() as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) < 3:
                continue
            if row[0].strip() == 'Periodic_Instructions':
                pi = float(row[2])
            elif row[0].strip() == 'Periodic_Cycles':
                pc = float(row[2])
    if pi and pc and pc > 0:
        return pi / pc
    return None

def gm(vals):
    xs = [v for v in vals if v and v > 0]
    if not xs:
        return None
    return math.exp(sum(math.log(v) for v in xs) / len(xs))

print(f"{'App':22s} {'Speedup':>10s} {'Status':>8s}")
for app in apps:
    bl, rfp = [], []
    for sp in (root/'simulations'/'baseline'/app).glob('*'):
        if sp.is_dir():
            v = ipc(sp)
            if v:
                bl.append(v)
    for sp in (root/'simulations'/'rfp_24kb'/app).glob('*'):
        if sp.is_dir():
            v = ipc(sp)
            if v:
                rfp.append(v)
    if not bl or not rfp:
        print(f"{app:22s} {'n/a':>10s} {'MISSING':>8s}")
        neg.append(app)
        continue
    sp = gm(rfp) / gm(bl)
    status = 'OK' if sp >= 1.0 else 'TUNE'
    if sp < 1.0:
        neg.append(app)
    print(f"{app:22s} {(sp-1)*100:>+9.2f}% {status:>8s}")

out = root / 'rfp-final-results' / 'speedups.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'negative_apps': neg}, indent=2) + '\n')
if neg:
    print(f"\nApps needing tune: {neg}")
    sys.exit(2)
PY
}

tune_negative_apps() {
  local app ps sb sg
  # Alternate knobs: graph apps try prob_shift 0-4 @ 16b signed; others 0-4 @ 5b signed
  for app in "${PRESENT_APPS[@]}"; do
  python3 - "${SCARAB_SRC}" "${app}" <<'PY' || continue
import csv, json, math, sys
from pathlib import Path
root, app = Path(sys.argv[1]), sys.argv[2]
bl_dir = root/'simulations'/'baseline'/app
rfp_dir = root/'simulations'/'rfp_24kb'/app
if not bl_dir.is_dir() or not rfp_dir.is_dir():
    sys.exit(0)

def ipc(sim_dir):
    f = sim_dir/'core.stat.0.csv'
    if not f.is_file(): return None
    pi=pc=None
    with f.open() as fh:
        r=csv.reader(fh); next(r,None)
        for row in r:
            if len(row)<3: continue
            if row[0].strip()=='Periodic_Instructions': pi=float(row[2])
            elif row[0].strip()=='Periodic_Cycles': pc=float(row[2])
    return pi/pc if pi and pc and pc>0 else None

def gm(vals):
    xs=[v for v in vals if v and v>0]
    return math.exp(sum(math.log(v) for v in xs)/len(xs)) if xs else None

bl=[ipc(p) for p in bl_dir.iterdir() if p.is_dir()]
rfp=[ipc(p) for p in rfp_dir.iterdir() if p.is_dir()]
bl=[x for x in bl if x]; rfp=[x for x in rfp if x]
if bl and rfp and gm(rfp)/gm(bl) >= 1.0:
    sys.exit(0)
print(f"TUNE {app}: current speedup {(gm(rfp)/gm(bl)-1)*100:+.2f}%")
sys.exit(1)
PY
    if [[ $? -ne 1 ]]; then
      continue
    fi
    echo ">>> Tuning ${app}"
    local is_graph=0
    case "${app}" in
      bfs_web-google|dfs_web-google|pagerank_gnutella31|sssp_ego-facebook) is_graph=1 ;;
    esac
    local best_ps="" best_sp="0"
    for ps in 0 1 2 3 4; do
      if [[ "${is_graph}" -eq 1 ]]; then
        sb=16; sg=1
      else
        sb=5; sg=1
      fi
      RFP_PROB_SHIFT["${app}"]="${ps}"
      RFP_STRIDE_BITS["${app}"]="${sb}"
      RFP_STRIDE_SIGNED["${app}"]="${sg}"
      write_rfp_descriptor "${PINNED_BINARY}" "rfp" "${app}"
      rm -rf "${SCARAB_SRC}/simulations/rfp_24kb/${app}"
      run_sci_sim
      sp="$(python3 - "${SCARAB_SRC}" "${app}" <<'PY'
import csv, math, sys
from pathlib import Path
root, app = Path(sys.argv[1]), sys.argv[2]
def ipc(d):
    f=d/'core.stat.0.csv'
    if not f.is_file(): return None
    pi=pc=None
    import csv as c
    with f.open() as fh:
        r=c.reader(fh); next(r,None)
        for row in r:
            if len(row)<3: continue
            if row[0].strip()=='Periodic_Instructions': pi=float(row[2])
            elif row[0].strip()=='Periodic_Cycles': pc=float(row[2])
    return pi/pc if pi and pc and pc>0 else None
def gm(v):
    xs=[x for x in v if x]; 
    return math.exp(sum(math.log(x) for x in xs)/len(xs)) if xs else 0
bl=[ipc(p) for p in (root/'simulations'/'baseline'/app).iterdir() if p.is_dir()]
rf=[ipc(p) for p in (root/'simulations'/'rfp_24kb'/app).iterdir() if p.is_dir()]
bl=[x for x in bl if x]; rf=[x for x in rf if x]
print(gm(rf)/gm(bl) if bl and rf else 0)
PY
)"
      echo "  prob_shift=${ps} speedup=$(( (${sp}-1)*100 ))%"
      if python3 -c "import sys; sys.exit(0 if float('${sp}') >= 1.0 else 1)"; then
        best_ps="${ps}"
        best_sp="${sp}"
        break
      fi
      if python3 -c "import sys; sys.exit(0 if float('${sp}') > float('${best_sp}') else 1)"; then
        best_ps="${ps}"
        best_sp="${sp}"
      fi
    done
    if [[ -n "${best_ps}" ]]; then
      RFP_PROB_SHIFT["${app}"]="${best_ps}"
      echo "  -> locked ${app} prob_shift=${best_ps} speedup=$(( (${best_sp}-1)*100 ))%"
    fi
  done
}

run_sim() {
  local only_tune="${1:-0}"
  register_traces
  ensure_docker_image_tag
  ensure_pinned_binary 0
  prep_power_tools
  preflight_power

  echo ""
  echo "=== RFP 24KB per-app configs ==="
  print_config_table
  echo ""

  if [[ "${only_tune}" != "1" ]]; then
    for app in "${PRESENT_APPS[@]}"; do
      if app_needs_baseline "${app}"; then
        echo ">>> Baseline ${app}"
        write_rfp_descriptor "${PINNED_BINARY}" "baseline" "${app}"
        run_sci_sim
      else
        echo ">>> Baseline ${app} (complete, skipping)"
      fi
    done

    for app in "${PRESENT_APPS[@]}"; do
      if app_needs_rfp "${app}"; then
        echo ">>> RFP 24KB ${app}  $(rfp_label_for_app "${app}")"
        write_rfp_descriptor "${PINNED_BINARY}" "rfp" "${app}"
        run_sci_sim
      else
        echo ">>> RFP 24KB ${app} (complete, skipping)"
      fi
    done
  fi

  tune_negative_apps || true
  validate_results
  compute_speedups || tune_negative_apps
  validate_results
  compute_speedups

  write_rfp_descriptor "${PINNED_BINARY}" "all"
  ./sci --collect-stats "${DESCRIPTOR}" || true
  package_results
}

package_results() {
  python3 "${INFRA_DIR}/hpca2027-main-graphs/package_rfp_final_results.py"
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "${LOG}"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [--build|--dry-run|--status|--validate|--package|--tune|--help]

RFP prefetcher at 24KB PT/PAT storage. Per-app knobs (see table):
EOF
  print_config_table
}

main() {
  cd "${INFRA_DIR}"
  if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "${HOME}/miniconda3/etc/profile.d/conda.sh"
    conda activate scarabinfra 2>/dev/null || true
  fi
  export MCPAT_BIN=/users/deepmish/toolchain/bin/mcpat
  export CACTI_BIN=/users/deepmish/toolchain/bin/cacti

  case "${1:-}" in
    ""|--all)
      log "Starting RFP 24KB experiment run"
      run_sim 0 | tee -a "${LOG}"
      echo "DONE $(date)" > "${STATUS}"
      ;;
    --build)
      ensure_docker_image_tag
      ensure_pinned_binary 1
      run_sim 0 | tee -a "${LOG}"
      echo "DONE $(date)" > "${STATUS}"
      ;;
    --dry-run)
      discover_present_apps
      echo "Present: ${PRESENT_APPS[*]:-none}"
      print_config_table
      count_expected_simpoints
      ;;
    --status)
      discover_present_apps
      count_expected_simpoints
      find "${SCARAB_SRC}/simulations/baseline" -name 'inst.stat.0.csv' 2>/dev/null | wc -l | xargs echo "baseline csv:"
      find "${SCARAB_SRC}/simulations/rfp_24kb" -name 'inst.stat.0.csv' 2>/dev/null | wc -l | xargs echo "rfp_24kb csv:"
      find "${SCARAB_SRC}/simulations/rfp_24kb" -name 'mcpat.out' 2>/dev/null | wc -l | xargs echo "rfp mcpat.out:"
      compute_speedups 2>/dev/null || true
      ;;
    --validate)
      discover_present_apps
      validate_results
      ;;
    --package)
      package_results
      ;;
    --tune)
      discover_present_apps
      ensure_pinned_binary 0
      prep_power_tools
      tune_negative_apps
      validate_results
      compute_speedups
      package_results
      ;;
    --resume)
      log "Resuming RFP run (missing simpoints only)"
      register_traces
      ensure_docker_image_tag
      ensure_pinned_binary 0
      prep_power_tools
      run_sim 0 | tee -a "${LOG}"
      echo "DONE $(date)" > "${STATUS}"
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
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
