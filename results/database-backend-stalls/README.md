# Database backend-stall characterization

This directory contains application-plus-dataset database characterization.
The old generic database bars remain under `results/backend-stalls` as
provenance and must not be overwritten.

Native screening runs on `amd162.utah.cloudlab.us` use AMD perf counters only
to select stable candidates. A candidate is eligible after two error-free runs
with throughput coefficient of variation no greater than 5%. The native
`stalled-cycles-backend / cycles` ratio is not the final paper result.

No MongoDB or MySQL DynamoRIO trace may be collected until Deepanjali approves
the selected configuration. After approval, the definitive classification
comes from weighted Scarab raw top-down slot counters using the Golden Cove
configuration, `--dcache_assoc 8`, 10M-instruction intervals, and zero Scarab
warmup.

The simulation descriptor lists the complete screening matrix so workload IDs
are fixed early. Before simulation, reduce it to the approved winner for each
database and add those trace descriptors to the workload database.

## Initial native screening result

Screening completed on July 10, 2026. No candidate reached the 30% native
backend-stall acceptance threshold:

- MySQL winner: `mysql_tpcc_100w_32t`, 14.41% median backend stalls and 4.63%
  throughput CV.
- MongoDB winner: `mongodb_ycsb_a_10m`, 10.42% median backend stalls and 3.67%
  throughput CV.
- YCSB C and F were rejected for throughput CV above 5%.
- YCSB E was rejected after the corrected analyzer found 499,385 failed inserts
  across its two measured runs. The warmup and measurement reused the same
  transaction-insert key range.

The MongoDB/MySQL matrix therefore stops at native screening. No MongoDB or
MySQL trace has been collected. See `advisor_approval_memo.md` for the decision
request and `native-screening-evidence-20260710` for the concise raw evidence.

## Expanded native screening result

An approved-style research expansion was completed on July 11, 2026 using
server-container cgroup counters, which exclude client and unrelated host
activity. Two stable candidates now exceed the 30% native threshold:

- MySQL: `mysql_tpch_sf10_q18_bp16g_mem32g_cgroup`, 32.10% median backend
  stalls, 0.15% throughput CV, 0.01% instruction CV, 0.18% cycle CV, 0 errors.
- MongoDB: `mongodb_ycsb10m_sort_aggregate_wt1g_mem3g_cgroup`, 36.26% median
  backend stalls, 0.13% throughput CV, 0.05% instruction CV, 0.01% cycle CV,
  0 errors.

The MySQL workload uses standard TPC-H SF10 Q18. The MongoDB workload uses the
existing 10M-record YCSB dataset and a reproducible aggregation pipeline that
sorts by `field0`, projects `_id`, and returns 100,000 rows with disk use
allowed. It is an analytical MongoDB input, not a standard YCSB transaction
profile, and must be described that way in paper text.

These percentages are candidate-selection evidence, not final Scarab top-down
results. Deepanjali's approval is still required before collecting either
DynamoRIO server trace. Raw counters, exact query/configuration, and strict
two-run validation are in `expanded-native-screening-20260711`.

## PostgreSQL TPC-H result

PostgreSQL TPC-H SF10 Q7 has completed native screening, a validated 120M
backend trace, and a full-window Golden Cove Scarab simulation. Its definitive
top-down result is 76.97% backend bound. The complete evidence and plot are in
`postgres-tpch-sf10-q7-20260710`.
