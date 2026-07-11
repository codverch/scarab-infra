# HPCA 2027 database backend-bound characterization

Date: 2026-07-10

## Goal and approval boundary

Select one MySQL and one MongoDB application+input pair whose validated Scarab
top-down result is at least 30% backend bound. Native screening is allowed
before advisor approval. DynamoRIO trace collection is not allowed until
Deepanjali approves the selected configuration.

The existing generic database results are provenance, not accepted inputs:

| Existing trace | Backend bound |
| --- | ---: |
| MongoDB | 4.538% |
| MySQL | 9.370% |
| PostgreSQL | 14.783% |

The claimed PostgreSQL+TPC-H result is provisional. No local artifact currently
records its TPC-H scale, query mix, raw counters, trace provenance, or SimPoint
weights, so it must not be reported complete until those are recovered and
audited.

## Fixed candidate matrix

Tanvir Ahmed Khan et al.'s Whisper evaluation uses MySQL with different TPC-C
queries and PostgreSQL with pgbench. It does not specify a MongoDB workload.
Accordingly, MySQL uses the paper-backed standard TPC-C mix, while MongoDB uses
an explicitly labeled YCSB exploration.

| Workload ID | Dataset and load | Clients | Warmup | Measurement |
| --- | --- | ---: | ---: | ---: |
| `mysql_tpcc_100w_32t` | TPC-C, 100 warehouses | 32 | 300 s | 600 s |
| `mysql_tpcc_100w_64t` | TPC-C, 100 warehouses | 64 | 300 s | 600 s |
| `mysql_tpcc_200w_64t` | TPC-C, 200 warehouses | 64 | 300 s | 600 s |
| `mongodb_ycsb_a_10m` | 10M records, 10 x 100-byte fields, 50/50 read/update | 64 | 5M ops | 5M ops |
| `mongodb_ycsb_c_10m` | same dataset, read-only | 64 | 5M ops | 5M ops |
| `mongodb_ycsb_e_10m` | same dataset, short range scans | 64 | 5M ops | 5M ops |
| `mongodb_ycsb_f_10m` | same dataset, read-modify-write | 64 | 5M ops | 5M ops |

Each candidate runs twice without DynamoRIO. Server cores are 0-23 and client
cores are 24-31. `perf stat` measures only server cores, avoiding Java client
events. The screen records throughput, latency, CPU utilization, IPC, cache
MPKI, branch MPKI, and backend stalled cycles as a percentage of cycles.

## Pinned software and storage

- Node: `Harry123@amd162.utah.cloudlab.us`, AMD EPYC 7302P, 32 logical CPUs.
- MySQL: official `mysql:8.0.45-debian`; record the resolved image digest.
- MongoDB: official `mongo:7.0.37-jammy`; record the resolved image digest.
- BenchBase: tag `v2023`, commit `b4b36683afdf79dd8bf70b95199b874c68218975`.
- YCSB: release `0.17.0`, tag commit `4b19340`; verify the downloaded SHA-256.
- Data root: `/mnt/hpca2027-db` on the node-local 447.13 GiB `/dev/sdb`.

Before formatting `/dev/sdb`, re-run `lsblk`, `blkid`, and `wipefs -n`. Abort
if any signature, partition, UUID, or mount appears, and obtain Harry's explicit
approval for `mkfs.ext4`.

## Candidate selection

`scripts/analyze_database_screening.py` aggregates the two repetitions. A
candidate is eligible only when both runs exist, errors are zero, throughput
CV is at most 5%, and the native backend-stall event is available. Select the
eligible candidate with the largest median backend-stalled-cycle percentage
for each database. Native AMD counters are a shortlist signal only; Scarab raw
slot counters make the definitive classification.

If no candidate is eligible, or if the approved candidate later measures below
30% Scarab backend bound, stop and ask Deepanjali before expanding the matrix.

## Post-approval trace protocol

Launch the selected database server under DynamoRIO and drive it from a
separate client. Timestamp server readiness, warmup, measurement, trace-window
activation, and first raw output. Reject any pilot whose trace does not fall
inside the measured phase.

After a 1M alignment pilot, collect a 1B global pilot to measure the largest
server thread's instruction share. Size the final global window to provide at
least 120M instructions in that thread, capped at 15.2B instructions and 300GB
predicted output. Convert with 10M chunks, validate every ZIP, and require
`basic_counts` to agree with the intended region.

Cluster the deterministic largest-instruction server thread, use zero Scarab
warmup, simulate each selected SimPoint with Golden Cove and
`--dcache_assoc 8`, and aggregate percentages with normalized SimPoint weights.
Archive the old generic database bars; do not overwrite their source files.

## Advisor approval message

> I finished native, non-tracing screening for the proposed database inputs.
> MySQL follows the TPC-C workload used in Tanvir's Whisper methodology, and
> MongoDB uses explicitly labeled YCSB profiles because that paper does not
> define a MongoDB input. The attached table includes two-run stability,
> throughput, and AMD backend-stall screening. May I collect DynamoRIO traces
> for `<selected MySQL ID>` and `<selected MongoDB ID>` and use them in the
> Scarab top-down graph? No traces have been collected yet.

