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

PARAMS_FILE="$SCARABHOME/src/PARAMS.$SCARABARCH"
if [[ "$SCARAB_BIN" =~ ^scarab_([0-9a-fA-F]+) ]]; then
  HASH="${BASH_REMATCH[1]}"
  PARAMS_BY_HASH="$SCARABHOME/src/PARAMS.$SCARABARCH.$HASH"
  if [ -f "$PARAMS_BY_HASH" ]; then
    PARAMS_FILE="$PARAMS_BY_HASH"
  fi
fi

SIMHOME=$SCENARIO/$WORKLOAD_HOME
mkdir -p $SIMHOME
OUTDIR=$SIMHOME

# Expand per-simpoint placeholders in Scarab params, e.g.
#   --ideal_fusion_log /home/<user>/ideal_fusion_candidates/{workload}/{cluster_id}.csv
APP_NAME="${WORKLOAD_HOME##*/}"
SCARABPARAMS="${SCARABPARAMS//\{workload\}/$APP_NAME}"
SCARABPARAMS="${SCARABPARAMS//\{cluster_id\}/$CLUSTER_ID}"
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
    # simultion uses the specific trace file
    # the roiStart is the second chunk, which is assumed to be segment size
    #### if chunk zero chunk is part of the simulation, the roiStart is the first chunk
    # the roiEnd is always the end of the trace -- (dynamorio uses 0)
    # the warmup is the same

    numChunk=$(unzip -l "$TRACEFILE" 2>/dev/null | grep -c "chunk\." || true)
    # Multi-chunk zips embed a prior segment before the simpoint; skip SEGSIZE instrs.
    # Single-chunk simpoint zips (e.g. some cluster_id != segment_id cases) start at 1
    # and contain roughly one segment of instructions, not segment_idx+1 segments.
    if [ "${numChunk:-0}" -le 1 ]; then
      roiStart=1
      # Only clamp to a single segment if a larger limit wasn't already requested
      # above (no-warmup + big --inst_limit case keeps the full requested limit).
      if [ "$instLimit" -le "$SEGSIZE" ]; then
        instLimit=$SEGSIZE
        if [ "${numChunk:-0}" -gt 0 ]; then
          instLimit=$(( numChunk * SEGSIZE ))
        fi
      fi
      # Single-chunk zips hold ~one segment; 10M warmup would consume the whole trace
      # and leave only *.csv.warmup (no bp.stat.0.csv). Measure the full zip instead.
      WARMUP=0
    fi

    # roiStart 1 means simulation starts with chunk 0
    if [ "$roiStart" == "1" ]; then
      #echo "ROISTART"
      #echo "$TRACEFILE"
      #echo "$segID"
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
    else
      #echo "!ROISTART"
      scarabCmd="$SCARABHOME/src/$SCARAB_BIN \
      --frontend memtrace \
      --cbp_trace_r0=$TRACEFILE \
      --memtrace_roi_begin=$(( $SEGSIZE + 1)) \
      --memtrace_roi_end=$(( $SEGSIZE + $instLimit )) \
      --inst_limit=$instLimit \
      --full_warmup=$WARMUP \
      --use_fetched_count=1 \
      $SCARABPARAMS \
      &> sim.log"
    fi
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
