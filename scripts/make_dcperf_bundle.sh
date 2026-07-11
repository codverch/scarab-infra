#!/usr/bin/env bash
# Package a converted 100M DCPerf trace into the standard SimPoint bundle
# layout (fingerprint/ simpoints/ traces/whole traces_simp/) like bfs/agentic.
# args: <workload> <trace_zip> <out_bundle_dir>
set -euo pipefail

W="$1"; TRACEZIP="$2"; H="$3"
R=/proj/datacntr-effcy-PG0/Harry123/hpca2027_dcperf
SCARAB=$R/src/scarab-hpca2027-characterization/src/build/opt/scarab
CHUNK=10000000

DRDIR=$(dirname "$(dirname "$TRACEZIP")")
RAWDIR=$DRDIR/raw
[ -s "$RAWDIR/modules.log" ] || { echo "missing modules.log in $RAWDIR"; exit 1; }

rm -rf "$H"; mkdir -p "$H"/{fingerprint/pieces,fingerprint/footprint_pieces,simpoints,traces/whole,traces_simp/trace,traces_simp/bin}
cp -al "$DRDIR" "$H/traces/whole/" 2>/dev/null || cp -a "$DRDIR" "$H/traces/whole/"

numChunk=$(unzip -l "$TRACEZIP" | grep -c "chunk\.")
echo "$CHUNK" > "$H/fingerprint/segment_size"
echo "[$W] chunks/segments: $numChunk"

# 1. BBV fingerprint per segment (scarab trace_bbv mode)
pids=()
for seg in $(seq 0 $((numChunk-1))); do
  D=$H/fingerprint/run_$seg; mkdir -p "$D"; cp $R/PARAMS.in "$D/PARAMS.in"
  (cd "$D" && "$SCARAB" --frontend memtrace \
     --cbp_trace_r0="$TRACEZIP" \
     --mode=trace_bbv_distributed \
     --segment_instr_count=$CHUNK \
     --memtrace_roi_begin=$((seg*CHUNK+1)) \
     --memtrace_roi_end=$((seg*CHUNK+CHUNK)) \
     --trace_bbv_output=$H/fingerprint/pieces/segment.$seg \
     --trace_footprint_output=$H/fingerprint/footprint_pieces/segment.$seg \
     --use_fetched_count=1 > sim.log 2>&1) &
  pids+=($!)
done
fail=0; for p in "${pids[@]}"; do wait "$p" || fail=1; done
[ "$fail" = 0 ] || { echo "[$W] BBV generation failed; see $H/fingerprint/run_*/sim.log"; exit 1; }
ls "$H/fingerprint/pieces" | head -3

# 2. gather fingerprint pieces
python3 $R/gather_fp_pieces.py "$H/fingerprint/pieces" "$numChunk" segment
cp "$H/fingerprint/pieces/bbfp" "$H/fingerprint/bbfp"

# 3. SimPoint clustering (maxK = round(sqrt(#segments)), as run_clustering.sh)
lines=$(wc -l < "$H/fingerprint/bbfp")
maxK=$(echo "(sqrt($lines)+0.5)/1" | bc)
echo "[$W] bbfp rows: $lines, maxK: $maxK"
$R/simpoint.bin -maxK "$maxK" -dim 100 -fixedLength off -numInitSeeds 10 \
  -loadFVFile "$H/fingerprint/bbfp" \
  -saveSimpoints "$H/simpoints/opt.p" \
  -saveSimpointWeights "$H/simpoints/opt.w" \
  -saveVectorWeights "$H/simpoints/vector.w" \
  -saveLabels "$H/simpoints/opt.l" \
  -coveragePct .99 > "$H/simpoints/simp.opt.log" 2>&1

# 4. 0.99-coverage trim -> opt.{p,w}.lpt0.99 (+ opt.w.2 variants)
python3 - "$H/simpoints" "$lines" <<'EOF'
import sys
sp, nseg = sys.argv[1], int(sys.argv[2])
pw = []
p = {int(c): int(s) for s, c in (l.split() for l in open(f"{sp}/opt.p"))}
w = {int(c): float(x) for x, c in (l.split() for l in open(f"{sp}/opt.w"))}
keep, cum = [], 0.0
for c in sorted(w, key=lambda c: -w[c]):
    keep.append(c); cum += w[c]
    if cum >= 0.99: break
keep_set = set(keep)
with open(f"{sp}/opt.p.lpt0.99","w") as f:
    for s, c in (l.split() for l in open(f"{sp}/opt.p")):
        if int(c) in keep_set: f.write(f"{s} {c}\n")
with open(f"{sp}/opt.w.lpt0.99","w") as f:
    for x, c in (l.split() for l in open(f"{sp}/opt.w")):
        if int(c) in keep_set: f.write(f"{x} {c}\n")
with open(f"{sp}/opt.w.2","w") as f:
    for c in sorted(w): f.write(f"{w[c]*nseg:g} {c}\n")
with open(f"{sp}/opt.w.2.lpt0.99","w") as f:
    for c in sorted(w):
        if c in keep_set: f.write(f"{w[c]*nseg:g} {c}\n")
print("clusters:", {c: (p[c], w[c]) for c in sorted(w)}, "kept:", sorted(keep_set))
EOF

# 5. package per-simpoint zips (warmup_chunks=1, bfs-style [c0, seg-1, seg])
bash $R/minimize_trace.sh "$RAWDIR" "$TRACEZIP" "$H/simpoints" 1 "$H/traces_simp"
mv "$H"/traces_simp/*.zip "$H/traces_simp/trace/"
cp "$RAWDIR/modules.log" "$H/traces_simp/bin/modules.log"

# 6. clustering info
python3 - "$H" "$TRACEZIP" "$DRDIR" <<'EOF'
import json, sys, os
H, tz, dr = sys.argv[1:4]
info = {"modules_dir": os.path.join(dr, "raw"), "whole_trace": tz,
        "dr_folder": os.path.basename(dr), "trace_file": os.path.basename(tz)}
json.dump(info, open(os.path.join(H, "trace_clustering_info.json"), "w"), indent=2, separators=(",", ":"))
EOF

echo "[$W] bundle done:"; ls "$H"; ls "$H/traces_simp/trace/"
