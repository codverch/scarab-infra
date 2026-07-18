# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## 50M + 50M per-app budget (split across simpoints)

`runtime_ifuse_50m_warmup_50m_app_budget.sh` targets **~50M warmup + ~50M measure per app** by setting, for each app with `n` simpoints:

| | Formula |
|--|---------|
| `full_warmup` | `50e6 // n` |
| `inst_limit` | `2 * (50e6 // n)` |

Scarab-infra cannot attach different params to different workloads in one descriptor, so the launcher writes one generated JSON per unique `n` under `generated/` and runs them into the same experiment.

Excludes `sssp_ego_fb`. Preview with `--dry-run`.

## Descriptors

| File | Experiment dir | Configs |
|------|----------------|---------|
| `runtime_ifuse_50m_warmup_50m_app_budget.json` | `simulations/runtime-ifuse-50m-app-budget/` | `baseline`, `runtime_ifuse` |

## Launch

```bash
cd ~/scarab-infra
./json/hpca2027/runtime_ifuse_50m_warmup_50m_app_budget.sh --dry-run   # budget table
./json/hpca2027/runtime_ifuse_50m_warmup_50m_app_budget.sh             # full run
./json/hpca2027/runtime_ifuse_50m_warmup_50m_app_budget.sh --sim-only
```
