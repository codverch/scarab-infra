# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## Simulation window

| Setting | Value |
|---------|-------|
| Descriptor `warmup` | `20,000,000` |
| Scarab `full_warmup` | `20,000,000` |
| Scarab `inst_limit` | `30,000,000` (20M warmup + 10M measured) |

Each run simulates from instruction 1 of the simpoint zip (no leading segment skip), up to `inst_limit` or EOF.

## Register traces (once per tmpfs refresh)

```bash
cd ~/scarab-infra
python3 -m scripts.register_local_traces \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --warmup 20000000 \
  --workloads apsp bc bfs community connected_components dfs haystack leveldb pagerank sssp_ego_fb triangle_counting
```

Traces source: [deepanjalimishra99/new-crono-traces](https://huggingface.co/datasets/deepanjalimishra99/new-crono-traces) (CRONO graph suite + haystack RAG + leveldb YCSB).

## Run simulations

```bash
cd ~/scarab-infra
./sci --sim hpca2027/baseline
./sci --sim hpca2027/ideal_fusion_pass1
./sci --sim hpca2027/ideal_fusion_pass2          # requires pass-1 candidates
./sci --sim hpca2027/ideal_fusion_unbounded_pass1 # Scarab hpca2027-unbounded-distance-ideal-fusion
./sci --sim hpca2027/ideal_fusion_unbounded_pass2
./sci --sim hpca2027/runtime_ifuse
# Helios: per-app knobs — use the launcher (not bare ./sci --sim hpca2027/helios)
./json/hpca2027/helios.sh --dry-run   # print knobs + pin binary
./json/hpca2027/helios.sh             # register + per-app sim (reuses cached Scarab)
./json/hpca2027/helios.sh --build     # rebuild Scarab once, then run
./sci --sim hpca2027/runtime_ifuse_train_threshold_sweep
./sci --sim hpca2027/runtime_ifuse_tt64_thresh_sweep
./sci --sim hpca2027/runtime_ifuse_ipc_close_sweep
./sci --sim hpca2027/rfp                          # Scarab hpca2027-rfp

./sci --collect-stats hpca2027/baseline
./sci --visualize hpca2027/baseline
```

`helios.sh` pins a hash-named Scarab binary so each app’s `./sci --sim` does **not**
rebuild (unlike `scarab_current`, which rebuilds when the scarab tree is dirty).
Docker image tags are reused/retagged; full image rebuild is avoided.

Rebuild Scarab only when source or the workload Dockerfile changed:

```bash
./sci --build-scarab hpca2027/runtime_ifuse
./json/hpca2027/helios.sh --build
./sci --build-scarab hpca2027/rfp
```

### Helios per-app configs (stores-off, `architecture: in` → `PARAMS.in`)

Helios knobs in `helios.sh` currently cover the original agentic/DB apps only. The
new CRONO graph workloads (`bfs`, `dfs`, `pagerank`, `sssp_ego_fb`, `community`,
`connected_components`, `triangle_counting`, `bc`, `apsp`) use the same graph-style
`T300/W64/I1/D10/stores-off` defaults when run via `helios.sh`.

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
| `rfp.json` | `rfp-storage-sweep` | `baseline` vs `rfp_{6,12,18,24}kb` (Table 1 PT/PAT storage sweep) |
