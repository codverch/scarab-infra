# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## Simulation window

| Setting | Value |
|---------|-------|
| Descriptor `warmup` | `20,000,000` |
| Scarab `full_warmup` | `20,000,000` |
| Scarab `inst_limit` | `30,000,000` (20M warmup + 10M measured) |

Each run simulates from instruction 1 of the simpoint zip (no leading segment skip), up to `inst_limit` or EOF.

Power modeling is enabled on all descriptors (`--power_intf_on 1`) with `--bindir` pointing at the staged Scarab `bin/` tree.

## Register traces (once per tmpfs refresh)

```bash
cd ~/scarab-infra
python3 -m scripts.register_local_traces \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --warmup 20000000 \
  --workloads appworld bfs-web-google clickhouse corebench dfs-web-google duckdb leveldb pagerank-gnutella31 rocksdb sssp-ego-facebook terminal_bench
```

Traces live under `/dev/shm/baseline/simpoint_traces` (appworld, bfs-web-google, clickhouse, corebench, dfs-web-google, duckdb, leveldb, pagerank-gnutella31, rocksdb, sssp-ego-facebook, terminal_bench).

## Run simulations

```bash
cd ~/scarab-infra
./sci --sim hpca2027/baseline
./sci --sim hpca2027/ideal-fusion-pass1
./sci --sim hpca2027/ideal-fusion          # pass 2 (fused); outputs under simulations/ideal-fusion/<app>/
./sci --sim hpca2027/ideal-fusion-pass2    # alias for ideal-fusion.json
./sci --sim hpca2027/ifuse
./sci --sim hpca2027/rfp
# Helios: per-app knobs — use the launcher (not bare ./sci --sim hpca2027/helios)
./json/hpca2027/helios.sh --dry-run
./json/hpca2027/helios.sh
./json/hpca2027/helios.sh --build

./sci --collect-stats hpca2027/baseline
./sci --visualize hpca2027/baseline
```

`helios.sh` pins a hash-named Scarab binary so each app `./sci --sim` does **not** rebuild.
Docker image tags are reused/retagged; full image rebuild is avoided.

Rebuild Scarab only when source or the workload Dockerfile changed:

```bash
./sci --build-scarab hpca2027/baseline
./sci --build-scarab hpca2027/ifuse
./json/hpca2027/helios.sh --build
./sci --build-scarab hpca2027/rfp
```

## Ideal fusion workflow

Pass-1 writes candidates to tmpfs (not under `simulations/`), using the same
app directory names as `simpoint_traces/` (e.g. `bfs-web-google`, `corebench`):

```text
/dev/shm/baseline/ideal_fusion_candidates/{app}/{simpoint}.csv
```

Simulation outputs use the same names under `simulations/<experiment>/{app}/`.
Do not remap these to underscored aliases in `workloads_db.json`.

## Descriptors

| JSON | Experiment | Config(s) |
|------|------------|-----------|
| `baseline.json` | `baseline` | `baseline` |
| `ideal-fusion-pass1.json` | `ideal-fusion-pass1` | `pass1` |
| `ideal-fusion.json` | `ideal-fusion` | `ideal-fusion` |
| `ideal-fusion-pass2.json` | `ideal-fusion` | `ideal-fusion` (alias) |
| `ifuse.json` | `ifuse` | `ifuse` |
| `helios.json` | `helios` | `helios` |
| `rfp.json` | `rfp` | `baseline` vs `rfp_{6,12,18,24}kb` |
