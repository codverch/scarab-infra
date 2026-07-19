# HPCA 2027 characterization plots

## Backend-bound stalls (baseline)

```bash
cd /users/deepmish/scarab-infra
python3 hpca2027-characterization/plot_topdown_backend_stalls.py \
  --root /users/deepmish/scarab/src/simulations/baseline \
  --out-dir hpca2027-characterization \
  --weights-db workloads/workloads_db.json \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --eval-backend-threshold -1
```

Outputs: `backend-stalls.png` / `.pdf`, CSVs.

## Helios + Ideal Fusion IPC (normalized to baseline)

Requires results under:
- `scarab/src/simulations/baseline/...`
- `scarab/src/simulations/helios/...`
- `scarab/src/simulations/ideal-fusion-pass2/pass2/...`

```bash
cd /users/deepmish/scarab-infra
python3 hpca2027-characterization/plot_helios_ideal_ipc.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --output-dir hpca2027-characterization
```

Outputs: `ipc-helios-ideal.png` / `.pdf`, `ipc_helios_ideal.csv`.
