# HPCA 2027 simulation descriptors

All experiments use a fixed window:

| Parameter | Value |
|-----------|-------|
| Warmup | 10M (`--full_warmup 10000000`) |
| Measured | 20M (`--inst_limit 30000000`) |

## Descriptors

| File | Experiment dir | Configs |
|------|----------------|---------|
| `baseline_10m_warmup_20m_run.json` | `simulations/baseline/` | `baseline` |
| `ideal_fusion_pass2_10m_warmup_20m_run.json` | `simulations/ideal-fusion/` | `pass2` |
| `runtime_ifuse_10m_warmup_20m_run.json` | `simulations/runtime-ifuse/` | `baseline`, `runtime_ifuse` |

## Launch runtime I-Fuse

```bash
cd ~/scarab-infra
./json/hpca2027/runtime_ifuse_10m_warmup_20m_run.sh            # build + sim + finalize
./json/hpca2027/runtime_ifuse_10m_warmup_20m_run.sh --sim-only
```

Or via `./sci`:

```bash
./sci --build-scarab hpca2027/runtime_ifuse_10m_warmup_20m_run
./sci --sim hpca2027/runtime_ifuse_10m_warmup_20m_run
./sci --sim hpca2027/baseline_10m_warmup_20m_run
./sci --sim hpca2027/ideal_fusion_pass2_10m_warmup_20m_run
```
