# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## Simulation window

| Setting | Value |
|---------|-------|
| Descriptor `warmup` | `20,000,000` |
| Scarab `full_warmup` | `20,000,000` |
| Scarab `inst_limit` | `30,000,000` (20M warmup + 10M measured) |

Each run simulates at most 30M fetched instructions: 20M warmup (stats reset at `full_warmup`), then up to 10M measured simulation.

## Register traces (once per tmpfs refresh)

```bash
cd ~/scarab-infra
python3 -m scripts.register_local_traces \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --warmup 20000000 \
  --workloads appworld bfs dfs pagerank sssp_ego_fb core_bench mlgym_fmnist \
    terminal_bench duckdb leveldb rocksdb clickhouse masstree silo
```

## Run simulations

```bash
cd ~/scarab-infra
./sci --sim hpca2027/baseline
./sci --sim hpca2027/ideal_fusion_pass1
./sci --sim hpca2027/ideal_fusion_pass2          # requires pass-1 candidates
./sci --sim hpca2027/ideal_fusion_unbounded_pass1 # Scarab hpca2027-unbounded-distance-ideal-fusion
./sci --sim hpca2027/ideal_fusion_unbounded_pass2
./sci --sim hpca2027/runtime_ifuse
./sci --sim hpca2027/helios
./sci --sim hpca2027/runtime_ifuse_train_threshold_sweep
./sci --sim hpca2027/runtime_ifuse_tt64_thresh_sweep
./sci --sim hpca2027/runtime_ifuse_ipc_close_sweep
./sci --sim hpca2027/rfp                          # Scarab hpca2027-rfp

./sci --collect-stats hpca2027/baseline
./sci --visualize hpca2027/baseline
```

Rebuild Scarab only when source or the workload Dockerfile changed:

```bash
./sci --build-scarab hpca2027/runtime_ifuse
./sci --build-scarab hpca2027/helios
./sci --build-scarab hpca2027/rfp
```

## Ideal fusion workflow

Pass-1 writes candidates to tmpfs (not under `simulations/`):

```text
/dev/shm/baseline/ideal_fusion_candidates/{app}/{simpoint}.csv
```

Unbounded-distance pass-1 uses:

```text
/dev/shm/baseline/ideal_fusion_candidates_unbounded/{app}/{simpoint}.csv
```

## Descriptors

| JSON | Experiment | Config(s) |
|------|------------|-----------|
| `baseline.json` | `baseline` | `baseline` |
| `ideal_fusion_pass1.json` | `ideal-fusion-pass1` | `pass1` |
| `ideal_fusion_pass2.json` | `ideal-fusion-pass2` | `pass2` |
| `ideal_fusion_unbounded_pass1.json` | `ideal-fusion-unbounded-pass1` | `pass1` |
| `ideal_fusion_unbounded_pass2.json` | `ideal-fusion-unbounded-pass2` | `pass2` |
| `runtime_ifuse.json` | `runtime-ifuse` | `runtime_ifuse` |
| `runtime_ifuse_train_threshold_sweep.json` | `runtime-ifuse-train-threshold-sweep` | `train_thresh_{10,100,1000,10000}` |
| `runtime_ifuse_tt64_thresh_sweep.json` | `runtime-ifuse-tt64-thresh-sweep` | `tt64_thresh_{10,100,1000,10000}` |
| `runtime_ifuse_ipc_close_sweep.json` | `runtime-ifuse-ipc-close-sweep` | FCT/TT/thresh/confidence sweep |
| `helios.json` | `helios` | `helios` |
| `rfp.json` | `rfp` | `baseline` (`--rfp_on 0`) vs `rfp` (`--rfp_on 1`) |
