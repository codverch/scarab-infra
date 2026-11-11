#!/bin/bash
# podrun.sh EXP CFG BIN APP SP [scarab params...]: one SimPoint, same command as scarab-infra's
# run_memtrace_single_simpoint.sh (explicit_roi), PARAMS.in = PARAMS.golden_cove, power model off.
R=/localdisk/deepanjm/isca2027-sims; EXP=$1; CFG=$2; BIN=$3; APP=$4; SP=$5; shift 5
T=$R/traces/isca2027/datacenter/$APP/traces/simp
D=$R/simulations/$EXP/$CFG/isca2027/datacenter/$APP/$SP
mkdir -p $D && cp $R/bin/PARAMS.golden_cove $D/PARAMS.in && cd $D
ROI_BEGIN=$(sed -n 's/^ROI_BEGIN=//p' $T/$SP.roi); WARMUP=$(sed -n 's/^WARMUP=//p' $T/$SP.roi)
MEASURE=$(sed -n 's/^MEASURE=//p' $T/$SP.roi); LIM=$((WARMUP + MEASURE))
LD_LIBRARY_PATH=$R/bin/lib $R/bin/$BIN --frontend memtrace --cbp_trace_r0=$T/$SP.zip \
  --memtrace_roi_begin=$ROI_BEGIN --memtrace_roi_end=$((ROI_BEGIN + LIM - 1)) --inst_limit=$LIM \
  --full_warmup=$WARMUP --use_fetched_count=1 --icache_size 32768 --power_intf_on 0 "$@" &> sim.log
rc=$?
echo "$EXP $CFG $APP $SP rc=$rc $(grep -m1 Periodic core.stat.0.out 2>/dev/null | tr -s ' ')" >> $R/simulations/$EXP/done.txt
