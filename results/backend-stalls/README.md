# Backend-stalls top-down characterization

This directory contains the reproducible result requested for the HPCA 2027
backend-stall characterization. It covers 18 datacenter and agentic workloads,
81 SimPoints, and the four Scarab top-down categories: Frontend bound, Bad
speculation, Retiring, and Backend bound.

## Methodology

- Scarab source: `codverch/scarab` branch `hpca2027-characterization`, commit
  `7185dea5`.
- Configuration: golden-cove `PARAMS.in` with `--dcache_assoc 8`.
- Every trace uses `full_warmup=0` as directed by the advisor.
- Each zip measures exactly its last trace chunk: for a zip containing `n`
  chunks of `C` instructions, ROI is `[(n-1)*C+1, n*C]` and `inst_limit=C`.
  This works for standard, prefix-packaged, and single-chunk bundles.
- Per-SimPoint percentages are calculated from raw slot counters. Workload
  values are `sum(normalized SimPoint weight * SimPoint percentage)`. The
  Average column is the arithmetic mean of the 18 workload values.
- Backend bound is the residual `100 - frontend - bad speculation - retiring`.

The stock infra launcher does not yet encode the last-chunk ROI rule when
warmup is zero. These runs therefore used the explicit windows recorded in
`run_manifest.csv`. The launcher semantics should be confirmed with the
advisor before replacing the direct-run manifest.

## Files

- `topdown_backend_stalls.csv`: one weighted row per workload plus Average.
- `topdown_backend_stalls_per_simpoint.csv`: all 81 raw slot-counter rows,
  source and normalized weights, and derived top-down percentages.
- `topdown_backend_stalls.png` and `.pdf`: generated stacked-bar figure.
- `run_manifest.csv`: exact trace path, ROI, warmup, and instruction limit for
  every simulation.
- `validation/result_audit.md`: completeness and provenance checks.
- `validation/bfs_weighted_verification.md`: independent hand calculation of
  one plotted data point.

## Regeneration

```bash
python3 scripts/plot_topdown_backend_stalls.py \
  --root /path/to/simulations_zero_warmup \
  --out-dir results/backend-stalls \
  --weights-db workloads/workloads_db.json
```

The plotting script fails if required counters or weights are missing, if two
SimPoints of one workload have identical raw top-down tuples, or if the
calculated backend residual is negative beyond tolerance.

## Trace provenance caveat

`memcached` and `redis` use the only bundles available on the Clemson node:
the `traces_lab` cluster037 copies. Confirm that these are the advisor's
intended official bundles before treating their values as final paper data.
