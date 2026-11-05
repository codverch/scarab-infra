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
leveldb, memcached, pagerank, terminal_bench (32 simpoints, the `lpt0.99` sets,
weights renormalized to 1).

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
| clickhouse | segment 17 | 10M warmup + 10M measured | the release holds only 10M before the selected interval (`trace_warmup_interval [0,10M]`) |

Warmup that covers another simpoint's measured segment (standard SimPoint
practice; stats are reset after warmup): appworld 5 over 4, core_bench 9 over 8,
duckdb 6 over 5.

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

Results: `results/isca2027/baseline/`.
