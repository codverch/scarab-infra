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

## 2026-07-10: DCPerf feedsim and taobench added

feedsim and tao (TaoBench) bars were added from the validated 100M DCPerf
traces on amd162 (largest-thread selection per the get_largest_trace policy,
whole-trace measurement, full_warmup=0, single weight 1.0; manifest rows use
roi 0,0 meaning no ROI window). Scarab commit 7185dea5, same golden_cove
PARAMS.in as all other bars. Backend bound: feedsim 23.54%, tao 28.50%.
Django's trace is pending delay calibration (launch-mode collection is
validated); MediaWiki is pending the JIT-tracing decision; VideoTranscode is
pending the CDVL dataset. See docs/hpca2027_dcperf_characterization_status.md.

## 2026-07-11: feedsim/tao switched to SimPoint bundles (bfs-style)

Both DCPerf workloads were recollected with larger budgets (feedsim 1.50B,
tao 0.99B fetched) so the largest server thread alone spans 13/17 x 10M
chunks, then packaged into standard SimPoint bundles on amd162 under
`hpca2027_dcperf/bundles/{feedsim,tao}` (fingerprint/, simpoints/,
traces_simp/trace/<segment>.zip in the [chunk.0000, seg-1, seg] layout,
scripts/make_dcperf_bundle.sh). Graph bars are now SimPoint-weighted,
zero-warmup target-chunk measurements:

- feedsim: segments 5/6/12, weights 0.484/0.484/0.032 -> 88.13% backend bound.
  The earlier whole-window single run (23.54%) blended request-handling
  phases; the weighted steady-state phases are strongly memory bound.
- tao: segments 9/1, weights renormalized from 0.291/0.472 (cluster 0, weight
  0.236, is excluded: all four member segments abort scarab 7185dea5 with a
  uop_generator OP_INV undecodable instruction, likely the TLS/crypto phase;
  documented in the bundle trace_clustering_info.json; SimPoint coverage
  76.4%) -> 28.61% backend bound.
