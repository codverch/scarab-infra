#!/bin/bash
source utilities.sh

#set -x #echo on

#echo "Running on $(hostname)"

# TODO: for other apps?
WORKLOAD_HOME="$1"
SCENARIO="$2"
SCARABPARAMS="$3"
# this is fixed/settled for NON trace post-processing flow.
# for trace post-processing flow, SEGSIZE is read from file
SEGSIZE="$4"
SCARABARCH="$5"
WARMUP="$6"
TRACE_WARMUP="$7"
TRACE_TYPE="$8"
SCARABHOME="$9"
CLUSTER_ID="${10}"
TRACEFILE="${11}"
SCARAB_BIN="${12}"
SEGMENT_IDX="${13}"

APP_NAME="${WORKLOAD_HOME##*/}"
# Prefer on-disk simpoint_traces dir names for result/candidate paths.
# Legacy underscore DB keys map onto hyphenated/compacted trace dirs.
case "$APP_NAME" in
  bfs_web-google) TRACE_DIR=bfs-web-google ;;
  dfs_web-google) TRACE_DIR=dfs-web-google ;;
  core_bench) TRACE_DIR=corebench ;;
  pagerank_gnutella31) TRACE_DIR=pagerank-gnutella31 ;;
  sssp_ego-facebook) TRACE_DIR=sssp-ego-facebook ;;
  *) TRACE_DIR="$APP_NAME" ;;
esac
SIMHOME=$SCENARIO/$TRACE_DIR
mkdir -p $SIMHOME
OUTDIR=$SIMHOME

PARAMS_FILE="$SCARABHOME/src/PARAMS.$SCARABARCH"
if [[ "$SCARAB_BIN" =~ ^scarab_([0-9a-fA-F]+) ]]; then
  HASH="${BASH_REMATCH[1]}"
  PARAMS_BY_HASH="$SCARABHOME/src/PARAMS.$SCARABARCH.$HASH"
  if [ -f "$PARAMS_BY_HASH" ]; then
    PARAMS_FILE="$PARAMS_BY_HASH"
  fi
fi

# Expand per-simpoint placeholders in Scarab params, e.g.
#   --ideal_fusion_log /home/<user>/ideal_fusion_candidates/{workload}/{cluster_id}.csv
#   --ifuse_fct_preload_file {root_dir}/pgo-candidates/.../{workload}/{cluster_id}.csv
# {root_dir} is the descriptor root_dir bind-mounted as $HOME inside the container.
# {workload} expands to the simpoint_traces dir name (TRACE_DIR), not legacy aliases.
SCARABPARAMS="${SCARABPARAMS//\{root_dir\}/$HOME}"
SCARABPARAMS="${SCARABPARAMS//\{workload\}/$TRACE_DIR}"
SCARABPARAMS="${SCARABPARAMS//\{cluster_id\}/$CLUSTER_ID}"
# Skip sims when a PGO FCT preload file is configured but absent for this simpoint.
if [[ "$SCARABPARAMS" == *"--ifuse_fct_preload_file"* ]]; then
  preload_path=$(echo "$SCARABPARAMS" | sed -n 's/.*--ifuse_fct_preload_file[ =]\([^ ]*\).*/\1/p')
  if [ -n "$preload_path" ] && [ ! -f "$preload_path" ]; then
    mkdir -p "$SIMHOME/$CLUSTER_ID"
    echo "SKIP: PGO preload file not found: $preload_path" > "$SIMHOME/$CLUSTER_ID/sim.log"
    exit 0
  fi
fi
# Pre-create the directory for --ideal_fusion_log so pass 1 can write candidates.
if [[ "$SCARABPARAMS" == *"--ideal_fusion_log"* ]]; then
  ideal_log_path=$(echo "$SCARABPARAMS" | sed -n 's/.*--ideal_fusion_log[ =]\([^ ]*\).*/\1/p')
  if [ -n "$ideal_log_path" ]; then
    mkdir -p "$(dirname "$ideal_log_path")"
  fi
fi

# cluster_id names the simpoint zip and output directory; segment_id drives ROI math
if [ -z "$SEGMENT_IDX" ]; then
  SEGMENT_IDX="$CLUSTER_ID"
fi

segID=$CLUSTER_ID
mkdir -p $OUTDIR/$segID
cp "$PARAMS_FILE" "$OUTDIR/$segID/PARAMS.in"
cd $OUTDIR/$segID

# CLUSTER_ID = -1 represents whole trace simulation
# CLUSTER_ID >= 0 represents segmented trace (simpoint) simulation
if [ "$CLUSTER_ID" == "-1" ]; then
  traceMap=$(ls $trace_home/$WORKLOAD_HOME/traces/whole/)
  scarabCmd="$SCARABHOME/src/$SCARAB_BIN \
  --frontend memtrace \
  --cbp_trace_r0=$trace_home/$WORKLOAD_HOME/traces/whole/${traceMap} \
  $SCARABPARAMS &> sim.log"
else
  # overwriting
  TRACEFILE=$trace_home/$WORKLOAD_HOME/traces/simp/$CLUSTER_ID.zip
  # Fall back to the raw (as-downloaded) HuggingFace layout when the registered
  # <suite>/<subsuite>/<workload>/traces/simp/<id>.zip symlink tree is absent.
  # That tree is created by scripts/register_local_traces.py under $trace_home,
  # which commonly lives on tmpfs (/dev/shm) and is wiped on reboot, whereas the
  # raw per-app trees (<app>/traces_simp/trace/<id>.zip) and workloads_db.json
  # persist. Searching the raw layout here lets sims run without re-registering.
  if [ ! -f "$TRACEFILE" ]; then
    for _app in "$TRACE_DIR" "$APP_NAME"; do
      for _cand in \
        "$trace_home/$_app/traces_simp/trace/$CLUSTER_ID.zip" \
        "$trace_home/$_app/traces_simp/$CLUSTER_ID.zip"; do
        if [ -f "$_cand" ]; then
          TRACEFILE="$_cand"
          break 2
        fi
      done
    done
  fi
  # Last resort: raw tree nested under a single per-instance dir
  # (e.g. <app>/<app_instance>/traces_simp/trace/<id>.zip).
  if [ ! -f "$TRACEFILE" ]; then
    for _app in "$TRACE_DIR" "$APP_NAME"; do
      _nested=$(ls "$trace_home/$_app"/*/traces_simp/trace/"$CLUSTER_ID.zip" 2>/dev/null | head -n 1)
      if [ -n "$_nested" ]; then
        TRACEFILE="$_nested"
        break
      fi
    done
  fi
  # roi is initialized by original segment boundary without warmup (use segment index, not cluster id)
  roiStart=$(( SEGMENT_IDX * $SEGSIZE + 1 ))
  roiEnd=$(( SEGMENT_IDX * $SEGSIZE + $SEGSIZE ))

  # now modify roi start based on warmup:
  # roiStart + WARMUP = original segment start
  if [ "$roiStart" -gt "$WARMUP" ]; then
    # enough room for warmup, extend roi start to the left
    roiStart=$(( $roiStart - $WARMUP ))
  else
    # insufficient preceding instructions, can only warmup till segment start
    WARMUP=$(( $roiStart - 1 ))
    # new roi start is the very first instruction of the trace
    roiStart=1
  fi

  instLimit=$(( $roiEnd - $roiStart + 1 ))

  # No warmup + an --inst_limit larger than one segment: start at instruction 1
  # (skip nothing) and honor the requested limit instead of jumping to the
  # segment boundary. This is what the ideal-fusion pass-1/pass-2 runs expect.
  if [ "$WARMUP" == "0" ] && [[ "$SCARABPARAMS" =~ --inst_limit[[:space:]]+([0-9]+) ]]; then
    desired_limit="${BASH_REMATCH[1]}"
    if [ "$desired_limit" -gt "$SEGSIZE" ]; then
      roiStart=1
      instLimit=$desired_limit
    fi
  fi

  if [ "$TRACE_TYPE" == "iterative_trace" ]; then
    # with no warmup
    # simultion always simulate the whole trace file with no skip

    numChunk=$(unzip -l "$TRACEFILE" 2>/dev/null | grep "chunk." | wc -l)
    instLimit=$((numChunk * 10000000))

    scarabCmd="$SCARABHOME/src/$SCARAB_BIN \
    --frontend memtrace \
    --cbp_trace_r0=$TRACEFILE \
    --inst_limit=$instLimit \
    --full_warmup=$WARMUP \
    --use_fetched_count=0 \
    $SCARABPARAMS \
    &> sim.log"
  elif [ "$TRACE_TYPE" == "trace_then_cluster" ]; then
    # Run from the first instruction in the simpoint zip; do not skip an initial
    # segment/chunk. inst_limit is min(zip size, --inst_limit from descriptor).
    numChunk=$(unzip -l "$TRACEFILE" 2>/dev/null | grep -c "chunk\." || true)
    roiStart=1
    instLimit=$SEGSIZE
    if [ "${numChunk:-0}" -gt 0 ]; then
      instLimit=$(( numChunk * SEGSIZE ))
    fi
    if [[ "$SCARABPARAMS" =~ --inst_limit[[:space:]]+([0-9]+) ]]; then
      desired_limit="${BASH_REMATCH[1]}"
      if [ "$desired_limit" -lt "$instLimit" ]; then
        instLimit=$desired_limit
      fi
    fi

    scarabCmd="$SCARABHOME/src/$SCARAB_BIN \
    --frontend memtrace \
    --cbp_trace_r0=$TRACEFILE \
    --memtrace_roi_begin=1 \
    --memtrace_roi_end=$instLimit \
    --inst_limit=$instLimit \
    --full_warmup=$WARMUP \
    --use_fetched_count=1 \
    $SCARABPARAMS \
    &> sim.log"
  elif [ "$TRACE_TYPE" == "cluster_then_trace" ]; then
    if [ "$WARMUP" -lt "$TRACE_WARMUP" ]; then
      scarabCmd="$SCARABHOME/src/$SCARAB_BIN \
      --frontend memtrace \
      --cbp_trace_r0=$TRACEFILE \
      --fast_forward=1 \
      --fast_forward_trace_ins=$(( $TRACE_WARMUP - $WARMUP )) \
      --inst_limit=$instLimit \
      --full_warmup=$WARMUP \
      --use_fetched_count=1 \
      $SCARABPARAMS \
      &> sim.log"
    else
      scarabCmd="$SCARABHOME/src/$SCARAB_BIN \
      --frontend memtrace \
      --cbp_trace_r0=$TRACEFILE \
      --inst_limit=$instLimit \
      --full_warmup=$WARMUP \
      --use_fetched_count=1 \
      $SCARABPARAMS \
      &> sim.log"
    fi
  fi
fi

#echo "simulating clusterID ${clusterID}, segment $segID..."
#echo "command: ${scarabCmd}"
eval $scarabCmd &
wait $!
