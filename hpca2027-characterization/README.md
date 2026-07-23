# HPCA 2027 characterization plots

Plot scripts live in `scarab-infra/hpca2027-characterization/`.
Results are written under `scarab/src/hpca2027-characterization-results/<plot>/`.

## Backend-bound stalls (baseline)

```bash
/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-characterization/plot_topdown_backend_stalls.py \
  --eval-backend-threshold -1
```

Outputs: `scarab/src/hpca2027-characterization-results/backend_stalls/` (`backend-stalls.png`, `.pdf`, CSVs).

```bash
cd /users/deepmish/scarab
git add src/hpca2027-characterization-results/backend_stalls/
git commit -m "Update HPCA characterization backend stall results."
```

## Helios + Ideal Fusion IPC (normalized to baseline)

Requires results under:
- `scarab/src/simulations/baseline/...`
- `scarab/src/simulations/helios/...`
- `scarab/src/simulations/ideal-fusion-pass2/pass2/...`

```bash
/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-characterization/plot_helios_ideal_ipc.py \
  --simulations-root /users/deepmish/scarab/src/simulations
```

Outputs: `scarab/src/hpca2027-characterization-results/ipc_helios_runtime_pgo_ideal/`.

## Helios + RFP + Ideal fusion

```bash
/users/deepmish/miniconda3/envs/scarabinfra/bin/python \
  /users/deepmish/scarab-infra/hpca2027-characterization/plot_helios_ideal_ipc.py \
  --simulations-root /users/deepmish/scarab/src/simulations \
  --schemes helios rfp ideal_fusion \
  --ideal-fusion-dir /users/deepmish/scarab/src/simulations/ideal-fusion \
  --ideal-fusion-config ideal-fusion \
  --allow-partial \
  --output-stem ipc-helios-rfp-ideal
```

Outputs: `scarab/src/hpca2027-characterization-results/ipc_helios_rfp_ideal/`.

```bash
cd /users/deepmish/scarab
git add src/hpca2027-characterization-results/ipc_helios_rfp_ideal/
git commit -m "Update HPCA characterization IPC results."
```
