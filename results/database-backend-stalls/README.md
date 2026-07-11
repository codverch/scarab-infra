# Database backend-stall characterization

This directory is reserved for the selected MongoDB and MySQL
application-plus-dataset results. The old generic database bars remain under
`results/backend-stalls` as provenance and must not be overwritten.

Native screening runs on `amd162.utah.cloudlab.us` use AMD perf counters only
to select stable candidates. A candidate is eligible after two error-free runs
with throughput coefficient of variation no greater than 5%. The native
`stalled-cycles-backend / cycles` ratio is not the final paper result.

No DynamoRIO trace may be collected until Deepanjali approves the selected
configuration. After approval, the definitive classification comes from
weighted Scarab raw top-down slot counters using the Golden Cove configuration,
`--dcache_assoc 8`, 10M-instruction intervals, and zero Scarab warmup.

The simulation descriptor lists the complete screening matrix so workload IDs
are fixed early. Before simulation, reduce it to the approved winner for each
database and add those trace descriptors to the workload database.

## Native screening result

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

The matrix therefore stops at native screening. No database trace has been
collected. See `advisor_approval_memo.md` for the decision request and
`native-screening-evidence-20260710` for the concise raw evidence.
