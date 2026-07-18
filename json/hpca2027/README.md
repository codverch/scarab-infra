# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces` (all apps with `traces_simp/trace/*.zip`).

## Full-trace, no warmup

| | Value |
|--|--|
| `full_warmup` | `0` |
| `inst_limit` | suite max SP size + 1M (each SP runs to EOF) |

Separate experiments — runtime I-Fuse does **not** also run baseline:

| Launcher | Descriptor | Experiment dir | Config |
|----------|------------|----------------|--------|
| `runtime_ifuse.sh` | `runtime_ifuse.json` | `simulations/runtime-ifuse/` | `runtime_ifuse` |
| `baseline.sh` | `baseline.json` | `simulations/baseline/` | `baseline` |

```bash
cd ~/scarab-infra
./json/hpca2027/baseline.sh --sim-only
./json/hpca2027/runtime_ifuse.sh --sim-only

# or:
./sci --sim hpca2027/baseline
./sci --sim hpca2027/runtime_ifuse
```
