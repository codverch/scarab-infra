# HPCA 2027 simulation descriptors

CRONO LiveJournal workloads under `/dev/shm/baseline/simpoint_traces`.

| Parameter | Value |
|-----------|-------|
| Warmup | 10M (`--full_warmup 10000000`) |
| Measured | 10M (`--inst_limit 20000000`) |

## Workloads

`bc`, `bfs`, `community`, `connected_components`, `dfs`, `pagerank`, `sssp`

## Descriptors

| File | Experiment dir | Configs |
|------|----------------|---------|
| `baseline_10m_warmup_10m_run.json` | `simulations/baseline/` | `baseline` |
| `ideal_fusion_pass2_10m_warmup_10m_run.json` | `simulations/ideal-fusion/` | `pass2` |
| `runtime_ifuse_10m_warmup_10m_run.json` | `simulations/runtime-ifuse/` | `baseline`, `runtime_ifuse` |

## Launch

```bash
cd ~/scarab-infra
./sci --build-scarab hpca2027/baseline_10m_warmup_10m_run
./sci --sim hpca2027/baseline_10m_warmup_10m_run

./json/hpca2027/runtime_ifuse_10m_warmup_10m_run.sh
./sci --sim hpca2027/ideal_fusion_pass2_10m_warmup_10m_run
```
