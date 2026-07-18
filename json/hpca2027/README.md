# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## Window

| | Value |
|--|--|
| `full_warmup` | `0` |
| `inst_limit` | suite max SP + 1M (run each SP to EOF) |

| Launcher | Experiment dir | Config |
|----------|----------------|--------|
| `runtime_ifuse.sh` | `simulations/runtime-ifuse/{app}/{sp}/` | `runtime_ifuse` only (threshold=1000) |
| `runtime_ifuse_train_threshold_sweep.sh` | `simulations/runtime-ifuse-train-threshold-sweep/{config}/{app}/{sp}/` | `train_thresh_{10,100,1000,10000}` |
| `baseline.sh` | `simulations/baseline/{app}/{sp}/` | `baseline` only |

## Fast path (recommended)

Do **not** rebuild Scarab/docker every time — that is what makes setup slow.

```bash
cd ~/scarab-infra
./json/hpca2027/baseline.sh          # register + sim + finalize (reuses cache)
./json/hpca2027/runtime_ifuse.sh

# Training-threshold sweep: promote PC pairs to FCT after N=10/100/1000/10000 obs
./json/hpca2027/runtime_ifuse_train_threshold_sweep.sh

# equivalent sci-only (after a one-time --dry-run if JSON is stale):
./sci --sim hpca2027/baseline
./sci --sim hpca2027/runtime_ifuse
./sci --sim hpca2027/runtime_ifuse_train_threshold_sweep
./json/hpca2027/baseline.sh --finalize
./json/hpca2027/runtime_ifuse.sh --finalize
./json/hpca2027/runtime_ifuse_train_threshold_sweep.sh --finalize
```

Only rebuild when Scarab source or the workload Dockerfile changed:

```bash
./json/hpca2027/runtime_ifuse.sh --build
./json/hpca2027/runtime_ifuse_train_threshold_sweep.sh --build
# or:
./sci --build-scarab hpca2027/runtime_ifuse
```
