# HPCA 2027 simulation descriptors

## Runtime I-Fuse, no warmup, full-trace measurement

`runtime_ifuse_no_warmup_full_trace.json` compares baseline vs runtime I-Fuse
on `appworld`, `bc`, `bfs`, `dfs`, `duckdb`, `leveldb`, and `pagerank` with:

- `warmup: 0` / `--full_warmup 0` (no warmup; all retired instructions count)
- `--inst_limit 200000000` (upper bound; Scarab stops at EOF if the zip is shorter)

Because `inst_limit` exceeds each workload's segment size, `run_memtrace_single_simpoint.sh`
starts at instruction 1 and measures the whole available zip contents.

Launch:

```bash
cd ~/scarab-infra
./json/hpca2027/run_runtime_ifuse_no_warmup.sh            # build + sim
./json/hpca2027/run_runtime_ifuse_no_warmup.sh --sim-only  # skip rebuild
./json/hpca2027/run_runtime_ifuse_no_warmup.sh --status
./json/hpca2027/run_runtime_ifuse_no_warmup.sh --collect-stats
./json/hpca2027/run_runtime_ifuse_no_warmup.sh --visualize
```

Or manually:

```bash
./sci --build-scarab hpca2027/runtime_ifuse_no_warmup_full_trace
./sci --sim hpca2027/runtime_ifuse_no_warmup_full_trace
```

## Runtime I-Fuse on GAP apps: 10M warmup + rest of trace

`runtime_ifuse_gap_10m_warmup_rest_of_trace.json` compares baseline vs runtime
I-Fuse on GAP workloads `bc`, `bfs`, `dfs`, and `pagerank` with:

- `--full_warmup 10000000` (10M warmup)
- `--inst_limit 200000000` (upper bound; Scarab stops at EOF, so the measured
  window is whatever remains after warmup)

Approximate measured windows: bc ~50M; bfs/dfs/pagerank ~80M.

Launch:

```bash
cd ~/scarab-infra
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_rest.sh            # build + sim
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_rest.sh --sim-only  # skip rebuild
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_rest.sh --status
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_rest.sh --collect-stats
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_rest.sh --visualize
```

## Runtime I-Fuse on GAP apps: 10M warmup + 10M measurement

`runtime_ifuse_gap_10m_warmup_10m_run.json` compares baseline vs runtime I-Fuse
on GAP workloads `bc`, `bfs`, `dfs`, and `pagerank` with:

- `--full_warmup 10000000` (10M warmup)
- `--inst_limit 20000000` (measured window is instructions 10M–20M)

Launch:

```bash
cd ~/scarab-infra
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_10m.sh            # build + sim
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_10m.sh --sim-only  # skip rebuild
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_10m.sh --status
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_10m.sh --collect-stats
./json/hpca2027/run_runtime_ifuse_gap_10m_warmup_10m.sh --visualize
```

## Runtime I-Fuse with 40M warmup and 10M measurement

`runtime_ifuse_10m_warmup_20m_run.json` (experiment name
`hpca2027_runtime_ifuse_40m_warmup_10m_run`) uses `--full_warmup 40000000` and
`--inst_limit 50000000` so the measured window is instructions 40M–50M.
