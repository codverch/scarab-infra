#!/bin/bash
# Battery view of the ISCA 2027 baseline run: one battery per simpoint.
# Progress = last Scarab heartbeat instruction count / (WARMUP + MEASURE) from
# that simpoint's .roi file; "Core 0 Finished" = full. --compact prints one line
# (for a status line).
ROOT=${ROOT:-/hot/deepanjm/isca2027}
EXP=${EXP:-isca2027-baseline}
CFG=${CFG:-baseline}
TR=$ROOT/traces/isca2027/datacenter
SIM=$ROOT/simulations/$EXP/$CFG/isca2027/datacenter

battery() {  # $1 = percent 0..100
  local f=$(( ($1 + 5) / 10 )) s="" i
  for ((i = 0; i < 10; i++)); do [ $i -lt $f ] && s+="▰" || s+="▱"; done
  printf "%s" "$s"
}

done_n=0; run_n=0; fail_n=0; total=0; sum=0
lines=""
for app_dir in "$TR"/*/; do
  app=$(basename "$app_dir")
  line=$(printf "%-15s" "$app")
  for roi in "$app_dir"traces/simp/*.roi; do
    c=$(basename "$roi" .roi)
    seg=$(sed -n 's/^# segment \([0-9]*\).*/\1/p' "$roi")
    lim=$(( $(sed -n 's/^WARMUP=//p' "$roi") + $(sed -n 's/^MEASURE=//p' "$roi") ))
    log=$SIM/$app/$c/sim.log
    total=$((total + 1))
    if [ ! -f "$log" ]; then pct=0; tag="·"
    elif grep -q "Core 0 Finished" "$log"; then pct=100; tag="✓"; done_n=$((done_n + 1))
    elif grep -qiE "error|assert|abort|Segmentation" "$log"; then
      n=$(grep -oP 'Heartbeat:.*?\{ \K[0-9]+' "$log" | tail -1); pct=$(( ${n:-0} * 100 / lim )); tag="✗"; fail_n=$((fail_n + 1))
    else
      n=$(grep -oP 'Heartbeat:.*?\{ \K[0-9]+' "$log" | tail -1); pct=$(( ${n:-0} * 100 / lim )); tag="▸"; run_n=$((run_n + 1))
    fi
    [ $pct -gt 100 ] && pct=100
    sum=$((sum + pct))
    line+=$(printf "  s%-3s %s %3d%%%s" "$seg" "$(battery $pct)" "$pct" "$tag")
  done
  lines+="$line"$'\n'
done

if [ "$1" == "--compact" ]; then
  printf "isca2027 baseline %s %d%%  ✓%d ▸%d ✗%d /%d\n" "$(battery $((sum / (total > 0 ? total : 1))))" \
    $((sum / (total > 0 ? total : 1))) $done_n $run_n $fail_n $total
else
  printf "%s" "$lines"
  printf "\nall %s %d%%   done %d  running %d  failed %d  of %d   (· queued ▸ running ✓ done ✗ failed)\n" \
    "$(battery $((sum / (total > 0 ? total : 1))))" $((sum / (total > 0 ? total : 1))) $done_n $run_n $fail_n $total
fi
