# Runtime I-Fuse experiment descriptors (HPCA 2027)

## `runtime_ifuse_10m_warmup_20M_run`

Compares **baseline** vs **runtime I-Fuse** on every app in
`/dev/shm/baseline/simpoint_traces` with a fixed window:

| Parameter | Value |
|-----------|-------|
| Warmup | 10M (`--full_warmup 10000000`) |
| Measured | 20M (`--inst_limit 30000000`) |
| Experiment dir | `scarab/src/simulations/runtime-ifuse/` |

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
./json/hpca2027/runtime_ifuse_10m_warmup_20M_run.sh            # build + sim + finalize
./json/hpca2027/runtime_ifuse_10m_warmup_20M_run.sh --sim-only
./json/hpca2027/runtime_ifuse_10m_warmup_20M_run.sh --status
./json/hpca2027/runtime_ifuse_10m_warmup_20M_run.sh --finalize
```

Or via `./sci` directly (nested layout until you run `--finalize`):

```bash
./sci --build-scarab hpca2027/runtime_ifuse_10m_warmup_20M_run
./sci --sim hpca2027/runtime_ifuse_10m_warmup_20M_run
./sci --collect-stats hpca2027/runtime_ifuse_10m_warmup_20M_run
./json/hpca2027/runtime_ifuse_10m_warmup_20M_run.sh --finalize
```
