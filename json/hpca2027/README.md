# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## Simulation window

| Setting | Value |
|---------|-------|
| Descriptor `warmup` | `20,000,000` |
| Scarab `full_warmup` | `20,000,000` |
| Scarab `inst_limit` | `30,000,000` (20M warmup + 10M measured) |

Each run simulates from instruction 1 of the simpoint zip (no leading segment skip), up to `inst_limit` or EOF.

## Average register-file utilization (integer physical RF)

Scarab does **not** print one ready-made “average RF utilization %” stat. It samples occupancy **every cycle** and accumulates two running totals. You divide them after the run.

| Counter in `core.stat.0.csv` | Meaning |
|-------------------------------|---------|
| `REG_FILE_INT_OCCUPIED_SUM` | Sum of occupied integer physical registers over all sampled cycles |
| `REG_FILE_INT_PHYS_REGS_SUM` | Sum of total integer physical register slots over all sampled cycles (280 regs × samples, when size is fixed) |

**Formula (integer / general-purpose physical RF):**

```text
int_rf_util_pct = 100 × REG_FILE_INT_OCCUPIED_SUM / REG_FILE_INT_PHYS_REGS_SUM
```

Vector RF (332 physical regs in `PARAMS.in`):

```text
vec_rf_util_pct = 100 × REG_FILE_VEC_OCCUPIED_SUM / REG_FILE_VEC_PHYS_REGS_SUM
```

**Prerequisites**

1. Scarab built with RF instrumentation (`hpca2027-characterization` commit `7185dea5d`, cherry-picked onto `hpca2027-baseline`).
2. `PARAMS.in` already has `--reg_renaming_scheme 1` and `--reg_table_integer_physical_size 280` — no extra flags in JSON `params`.

**Workflow**

```bash
cd ~/scarab-infra
./sci --build-scarab hpca2027/baseline   # after pulling RF instrumentation
./sci --sim hpca2027/baseline
# Per simpoint: <sim_dir>/core.stat.0.csv → use 3rd column (cumulative total) for the two INT counters above
./sci --collect-stats hpca2027/baseline    # optional: simpoint-weighted tables in collected_stats.csv
```

`baseline.json` lists the `REG_FILE_*` counters under `visualize` / `perf_analyze` so `--collect-stats` and `--visualize` pull them; the **percentage is always computed offline** from the ratio of the two sums.

## Register traces (once per tmpfs refresh)

```bash
cd ~/scarab-infra
python3 -m scripts.register_local_traces \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --warmup 20000000 \
  --workloads appworld bfs clickhouse core_bench dfs duckdb pagerank rocksdb terminal_bench
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

| App | Config |
|-----|--------|
| `terminal_bench` | `T100/W64/I1/D10/stores-off` |
| `bfs` | `T300/W64/I1/D10/stores-off` |
| `dfs` | `T300/W64/I1/D10/stores-off` |
| `pagerank` | `T300/W64/I1/D10/stores-off` |
| `core_bench` | `T1000/W64/I1/D10/stores-off` |
| `appworld` | `T10000/W64/I1/D10/stores-off` |
| `rocksdb` | `T4800/W64/I1/D10/stores-off` |
| `duckdb` | `T30000/W64/I1/D10/stores-off` |
| `clickhouse` | `T300/W64/I10/D10/stores-off` |

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
