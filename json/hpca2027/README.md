# Runtime I-Fuse experiment descriptors (HPCA 2027)

## `runtime_ifuse_20m_warmup_rest_of_trace`

Compares **baseline** vs **runtime I-Fuse** on every app in
`/dev/shm/baseline/simpoint_traces` with:

| Parameter | Value |
|-----------|-------|
| Warmup | 20M (`--full_warmup 20000000`) |
| Measured | rest of each simpoint zip (`--inst_limit 200000000` ceiling; stops at EOF) |
| Experiment dir | `scarab/src/simulations/runtime-ifuse/` |

Approximate measured windows after 20M warmup: `sssp_ego_fb` ~10M; most
60M zips ~40M; `bfs`/`dfs`/`pagerank` ~70M.

### Result layout (after finalize)

```
simulations/runtime-ifuse/
  baseline/{app}/{simpoint}/
  runtime_ifuse/{app}/{simpoint}/
  collected_stats.csv
  .gitignore
```

Suite/subsuite nesting (`datacenter/datacenter/`), job `logs/`, and Scarab
binaries are removed by `--finalize` so the tree is easy to commit.

### Launch

```bash
cd ~/scarab-infra
./json/hpca2027/runtime_ifuse_20m_warmup_rest_of_trace.sh            # build + sim + finalize
./json/hpca2027/runtime_ifuse_20m_warmup_rest_of_trace.sh --sim-only
./json/hpca2027/runtime_ifuse_20m_warmup_rest_of_trace.sh --status
./json/hpca2027/runtime_ifuse_20m_warmup_rest_of_trace.sh --finalize
```

Or via `./sci` directly (nested layout until you run `--finalize`):

```bash
./sci --build-scarab hpca2027/runtime_ifuse_20m_warmup_rest_of_trace
./sci --sim hpca2027/runtime_ifuse_20m_warmup_rest_of_trace
./sci --collect-stats hpca2027/runtime_ifuse_20m_warmup_rest_of_trace
./json/hpca2027/runtime_ifuse_20m_warmup_rest_of_trace.sh --finalize
```
