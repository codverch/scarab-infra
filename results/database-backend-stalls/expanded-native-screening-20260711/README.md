# Expanded database native screening evidence

This folder preserves the two stable application-plus-input candidates found
on `amd162.utah.cloudlab.us` on July 11, 2026. Counters were collected only for
the database server Docker cgroup. Runs 1 and 2 are the acceptance repetitions;
run 99 is a pilot and is not included in the strict summaries.

## Accepted candidates

| Workload ID | Run 1 | Run 2 | Median | Stability |
| --- | ---: | ---: | ---: | --- |
| `mysql_tpch_sf10_q18_bp16g_mem32g_cgroup` | 32.7404% | 31.4559% | 32.0982% | throughput CV 0.1459%, instruction CV 0.0092%, cycle CV 0.1770% |
| `mongodb_ycsb10m_sort_aggregate_wt1g_mem3g_cgroup` | 37.3868% | 35.1295% | 36.2581% | throughput CV 0.1308%, instruction CV 0.0529%, cycle CV 0.0073% |

All four measured runs exited successfully and the analyzer found zero
workload errors. The acceptance threshold is 30% median native
`stalled-cycles-backend / cycles`, with throughput, instruction, and cycle CV
no greater than 5%.

## Layout

- `mysql-tpch/queries/q18.sql`: exact normalized MySQL query.
- `mysql-tpch/screening/.../run{1,2}`: raw server-cgroup perf counters,
  duration, workload output, CPU samples, cgroup identity, and manifests.
- `mysql-tpch/analysis-q18-final`: strict two-run analyzer output.
- `working-set-screening/.../run{1,2}`: equivalent MongoDB evidence plus
  WiredTiger cache and container-memory evidence.
- `mongodb-analysis`: strict two-run analyzer output.
- `software.json`: immutable database image digests and YCSB version checksum.

The reusable launchers are `scripts/run_mysql_tpch_screening.sh` and
`scripts/run_database_workingset_screening.sh`; analysis is implemented by
`scripts/analyze_database_screening.py`.

## Interpretation

These are native screening results used to select trace targets. AMD native
backend-stall counters are not equivalent to Scarab's Golden Cove top-down slot
model. Final claims require approved DynamoRIO server traces, SimPoint weights,
Scarab simulation, and manual verification of the weighted raw-slot result.

The MongoDB candidate is a custom analytical aggregation over the 10M-record
YCSB dataset, not a standard YCSB A-F profile. Its pipeline sorts by `field0`,
projects `_id`, limits output to 100,000 records, and permits disk use.

Methodology references: the TPC-H query definitions come from the official
[TPC-H specification](https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.18.0.pdf),
and the MongoDB cache setup follows the documented interaction between the
[WiredTiger cache and filesystem cache](https://www.mongodb.com/docs/v7.0/core/wiredtiger/).
