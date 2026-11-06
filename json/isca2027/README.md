# ISCA 2027 (I-Fuse) simulations

## Baseline

| Item | Value |
|------|-------|
| Scarab | `codverch/ifuse` branch `baseline` @ `89b5145`, binary `scarab_89b5145_0.opt` |
| Parameters | `src/PARAMS.in` (identical to `PARAMS.golden_cove`) + `--icache_size 32768 --power_intf_on 1` |
| Window | every simpoint: 20M warmup, then 10M measured on its own segment |
| Power | McPAT/CACTI from `codverch/mcpat` + Scarab `bin/power` patches, built in the `allbench_traces` image |
| Traces | HF `deepanjalimishra99/hpca2027-final-traces` (8 apps) + `hpca2027-ifuse` (cachebench, clickhouse, memcached) |

Applications: appworld, bfs, cachebench, clickhouse, core_bench, dfs, duckdb,
leveldb, memcached, pagerank, terminal_bench (40 simpoints; the `lpt0.99` sets,
weights renormalized to 1, except cachebench and clickhouse below).

## Simulation windows

The trace packages do not share one layout, so scarab-infra's
`cluster_id * segment_size` arithmetic cannot place the window. In particular,
for prefix zips (appworld, duckdb, leveldb) the stock `trace_then_cluster`
start of `segment_size + 1` makes every simpoint of a workload simulate the
same chunks. `scripts/register_isca2027_traces.py` therefore pins each window
explicitly:

- measured = the first 10M instructions of the selected segment S;
- warmup = the 20M instructions that precede S in the original program, contiguous;
- offsets are DynamoRIO fetched-instruction ordinals inside the zip, the units
  `--memtrace_roi_begin` uses (`skip_instructions`). Chunk offsets are exact
  because every zip's total satisfies `(n-1)*chunk < total <= n*chunk`.

It writes `traces/simp/<cluster>.zip` (hard link) + `<cluster>.roi`, an
`explicit_roi` entry in `workloads_db.json`, and `windows_audit.json` (every
window in program coordinates). `run_memtrace_single_simpoint.sh` reads the
`.roi` file in `explicit_roi` mode. Measured windows are pairwise disjoint
within every workload (checked).

Exceptions, all recorded in `windows_audit.json`:

| Workload | Simpoint | Window | Why |
|----------|----------|--------|-----|
| appworld | segment 9 | 20M warmup + 9.35M measured | the program ends 9.35M into segment 9 |

Warmup that covers another simpoint's measured segment (standard SimPoint
practice; stats are reset after warmup): appworld 5 over 4, core_bench 9 over 8,
duckdb 6 over 5.

## cachebench and clickhouse: 5 simpoints each

The releases hold one simpoint each. `scripts/trace_bbv.py` builds 10M-segment
BBVs from the trace itself; SimPoint 3.2 with k=5 fills only 2 (cachebench) and
3 (clickhouse) clusters, i.e. each is a single phase. So both use 5 evenly
spaced segments at weight 0.2 (systematic sampling), same 20M+10M windows:

- cachebench: segments 2, 4, 6, 7, 9 of the released trace.
- clickhouse: re-traced (pod, AVX2) from the released binary and query; the
  worker thread's first 260M instructions (of 7.44B), converted to ARCH_REGDEPS;
  segments 2, 8, 14, 19, 25.

Selections: `json/isca2027/recluster/*.json`, applied with
`register_isca2027_traces.py --override WORKLOAD=JSON`; descriptor
`baseline_recluster.json`.

Known trace property: Scarab's memtrace reader replaces about 0.6% of instructions with a
jump where the trace has a PC gap ("Patching gap in trace"). It affects
every configuration equally.

## Reproduce

```bash
# phoebe-login: apptainer, images at ~/sif -> /hot/deepanjm/sif
export PATH=/hot/deepanjm/sqfs-env/bin:/h/deepanjm/miniconda3/envs/scarabinfra/bin:$PATH
python3 scripts/register_isca2027_traces.py \
  --release-dir /hot/deepanjm/hpca2027-final-traces \
  --traces-dir /hot/deepanjm/isca2027/traces \
  --dr-launcher <scarab>/src/build/opt/deps/dynamorio/clients/bin64/drmemtrace_launcher \
  --workloads appworld bfs cachebench clickhouse core_bench dfs duckdb leveldb memcached pagerank terminal_bench \
  --audit json/isca2027/windows_audit.json
./sci --build-scarab isca2027/baseline
./sci --sim isca2027/baseline
./sci --collect-stats isca2027/baseline
json/isca2027/progress.sh          # battery per simpoint
```

Results: `codverch/ifuse` branch `baseline`, folder `baseline/isca2027/`.
