# Result audit

Validated on 2026-07-09 after the zero-warmup rerun.

## Completeness

- Descriptor workloads: 18.
- Expected SimPoints from `workloads/workloads_db.json`: 81.
- Manifest jobs: 81.
- Missing or unexpected workload/cluster pairs: 0.
- `PARAMS.out` files: 81.
- `core.stat.0.csv` files: 81.
- Files containing all required raw top-down counters: 81.
- Runtime failures: 0.

## Effective-parameter audit

Every `PARAMS.out` was compared with its row in `run_manifest.csv` for:

- `--cbp_trace_r0`
- `--memtrace_roi_begin`
- `--memtrace_roi_end`
- `--full_warmup`
- `--inst_limit`

All 81 matched exactly, and all 81 use `--full_warmup 0`.

The prefix-packaged `chemcrow/3` smoke run was checked before the full sweep:
ROI `30000001..40000000`, `inst_limit=10000000`, `full_warmup=0`, successful
completion, and all five raw top-down counters present.

## Data-quality checks

- The plot script's duplicate guard passed: all 81 workload/SimPoint raw
  top-down tuples are distinct within their workload.
- Every backend-bound residual is non-negative.
- The top-down sum is 100% by construction because Backend bound is the
  residual; this is not treated as an independent correctness check.
- The BFS weighted backend-bound value was independently reproduced to full
  displayed precision; see `bfs_weighted_verification.md`.

## Binary provenance

- Scarab binary SHA-256:
  `f3801544bc50af90c968a5fd07f3bb09bdf0be489631b1364a1cc925b027dbdf`
- DynamoRIO library SHA-256:
  `ce2d05d4964eee02273add6a5ad5ca85051b82e52b095ce79038062a0c05481e`
- Golden-cove `PARAMS.in` SHA-256:
  `5de095b6fe7b74011db99ecfc3f53e2f96c2f3b44f89fb4e9e149c7aad33fb33`

The full simulation directories are retained locally and on the Clemson node;
the compact per-SimPoint CSV in this result directory contains the raw
counters needed to reproduce the plot without committing simulator logs.
