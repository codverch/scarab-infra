# PostgreSQL TPC-H SF10 Q7 characterization

This package replaces the old generic PostgreSQL characterization with an
explicit application-plus-input result: `postgres_tpch_sf10_q7`.

## Result

The 120M-instruction Scarab window is 76.97% backend bound:

| Category | Slots (%) |
| --- | ---: |
| Frontend bound | 0.68 |
| Bad speculation | 0.66 |
| Retiring | 21.69 |
| Backend bound | 76.97 |

The categories were recomputed from Scarab's raw slot counters:

- total slots: 698,518,080
- issued slots: 154,667,398
- retired slots: 151,528,925
- frontend bubble slots: 4,747,897
- recovery bubble slots: 1,477,019

The calculation is:

```text
frontend = fetch_bubbles / total
bad_spec = (issued - retired + recovery_bubbles) / total
retiring = retired / total
backend = 1 - frontend - bad_spec - retiring
```

The unrounded categories sum to 100%, and backend bound is non-negative.

## Candidate selection

PostgreSQL 16.10 was loaded with a compliant TPC-H scale factor 10 database.
All 22 queries received one warmup and two measured native runs. Q7 was the
highest stable candidate after requiring runtime, instruction-count, and
cycle-count coefficients of variation no greater than 5%:

- median native backend stalls: 42.99%
- mean duration: 10.58 seconds
- duration CV: 0.38%
- instruction CV: 0.30%
- cycle CV: 0.82%

Q20 had a higher apparent native ratio but was rejected because its two
instruction counts differed by 31.60%. Native AMD counters were used only for
candidate selection; the table above comes from Scarab raw top-down slots.

## Trace and simulation

- Image: `postgres:16.10-bookworm`, resolved digest in `software.json`.
- TPC-H kit commit: `852ad0a5ee31ebefeed884cea4188781dd9613a3`.
- Trace target: PostgreSQL single-user backend executing Q7.
- Trace delay: 100,000,000 instructions.
- Trace request: 121,000,000 instructions with a strict 120,000,000 minimum.
- Validated fetched instructions: 120,961,104.
- Trace chunk size: 10,000,000 instructions.
- Trace ZIP SHA-256: `a6201cd94025f76f5a13bc3f8f2567f4eec9759c131ad82e054d7655a308806f`.
- Trace ZIP size: 59,878,422 bytes.
- Scarab git revision: `7185dea`.
- Scarab configuration: Golden Cove baseline with `--dcache_assoc 8`.
- Simulated instructions: 119,947,917 of the validated trace window.
- SimPoint weighting: not needed; the complete 120M window was simulated
  directly, so no sampled points or normalized weights enter this result.

The trace remains on `amd162.utah.cloudlab.us` under
`/mnt/hpca2027-db/postgres-tpch/traces/postgres_tpch_sf10_q7_121m_min120m_delay100m_20260710`.
The 59.9 MB trace ZIP is intentionally not committed to Git.

## Folder contents

- `native-screening/`: all-query summaries and both raw Q7 native runs.
- `trace-validation/`: fetched-instruction count, ZIP test, conversion log,
  alignment timestamps, and trace summary.
- `scarab-simulation/`: exact command, `PARAMS.in/out`, completion log, and
  raw `core.stat.0.csv`.
- `raw_topdown_counters.csv`: counters used by the calculation.
- `topdown_breakdown.csv`: unrounded percentages.
- `topdown_breakdown.png` and `.pdf`: meeting-ready plot.

Recreate the plot with:

```bash
scripts/plot_postgres_tpch_topdown.py \
  --core-stat results/database-backend-stalls/postgres-tpch-sf10-q7-20260710/scarab-simulation/core.stat.0.csv \
  --out-dir results/database-backend-stalls/postgres-tpch-sf10-q7-20260710
```
