# HPCA 2027 characterization: backend-bound stalls

Weighted TopDown backend-bound stall % for the full-trace baseline
(CD / CC omitted).

## Regenerate

```bash
cd /users/deepmish/scarab-infra
python3 hpca2027-characterization/plot_topdown_backend_stalls.py \
  --root /users/deepmish/scarab/src/simulations/baseline \
  --out-dir hpca2027-characterization \
  --weights-db workloads/workloads_db.json \
  --traces-dir /dev/shm/baseline/simpoint_traces \
  --eval-backend-threshold -1
```

## Outputs

- `topdown_backend_stalls.png` / `.pdf`
- `topdown_backend_stalls.csv`
- `topdown_backend_stalls_per_simpoint.csv`
