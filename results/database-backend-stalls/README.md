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
