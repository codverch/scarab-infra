# Database characterization decision memo

## Decision requested

The expanded native matrix found one stable candidate above 30% for each
database. Please confirm whether to collect DynamoRIO server traces for MySQL
TPC-H SF10 Q18 and the MongoDB analytical sort described below. No MySQL or
MongoDB DynamoRIO trace has been collected.

## Basis and setup

- Tanvir's Whisper workload table identifies MySQL with TPC-C queries. It does
  not specify a MongoDB workload, so MongoDB was screened with standard YCSB
  A, C, E, and F profiles.
- Node: `amd162.utah.cloudlab.us`, with server CPUs 0-23 and client CPUs 24-31.
- MySQL: official 8.0.45 image, 32 GiB InnoDB buffer pool, 300-second warmup,
  and 600-second measurement.
- MongoDB: official 7.0.37 image, 10M records of approximately 1 KiB, 64 client
  threads, 5M warmup operations, and 5M measured operations.
- BenchBase: v2023 at commit
  `b4b36683afdf79dd8bf70b95199b874c68218975`.
- YCSB: 0.17.0 archive SHA-256
  `6a054a706812269c80bfc6ed1e83457990c1c60b01f5083c873aaed05577e30d`.

The exact resolved container digests are in
`native-screening-evidence-20260710/manifests/software.json`.

## Results

| Candidate | Throughput | CV | Backend stalls | CPU | Errors | Eligible |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| MySQL 100w/32t | 2283.5 req/s | 4.63% | 14.41% | 65.45% | 0 | yes |
| MySQL 100w/64t | 1802.1 req/s | 2.53% | 14.29% | 53.09% | 0 | yes |
| MySQL 200w/64t | 1864.7 req/s | 13.20% | 14.98% | 69.63% | 0 | no |
| MongoDB YCSB A | 81057.2 ops/s | 3.67% | 10.42% | 86.04% | 0 | yes |
| MongoDB YCSB C | 75952.9 ops/s | 27.74% | 7.84% | 90.73% | 0 | no |
| MongoDB YCSB E | 26489.9 ops/s | 0.01% | 13.57% | 58.01% | 499385 | no |
| MongoDB YCSB F | 60104.2 ops/s | 6.53% | 10.64% | 89.53% | 0 | no |

YCSB E is invalid despite stable throughput. Its warmup inserted new records,
then measurement restarted transaction inserts at the same key range. The
generic failure parser now counts every YCSB `*-FAILED` operation category.

## Recommendation

Do not trace the current candidates. Ask whether to expand MySQL beyond the
three TPC-C configurations and whether to rerun YCSB E with a pristine database
per repetition plus a disjoint measured insert range. If expansion is approved,
native screening should still precede tracing.

After a candidate reaches the agreed threshold, the proposed trace protocol is
a 1M-instruction calibration, a 1B global pilot, then a final server trace sized
for at least 120M instructions in the largest thread, capped at 15.2B global
instructions and 300 GiB predicted storage.

## Expanded results

Native counters were restricted to each database server's Docker cgroup. This
avoids including client or unrelated host work in the selection metric.

| Candidate | Backend stalls | Throughput CV | Instruction CV | Cycle CV | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| MySQL TPC-H SF10 Q18, 16 GiB buffer pool, 32 GiB container | 32.10% | 0.15% | 0.01% | 0.18% | 0 |
| MongoDB YCSB-10M analytical sort, 1 GiB WT cache, 3 GiB container | 36.26% | 0.13% | 0.05% | 0.01% | 0 |

MySQL Q18 is the standard TPC-H large-volume-customer query. The MongoDB input
sorts the existing 10M-record YCSB collection by `field0`, projects `_id`, and
returns 100,000 rows with `allowDiskUse:true`. It should be labeled as a custom
analytical MongoDB workload, not as YCSB A-F. The raw evidence is under
`expanded-native-screening-20260711`.

## PostgreSQL audit

PostgreSQL+TPC-H is now verified. A full SF10 screen selected stable Q7 at
42.99% native backend stalls. A validated single-backend trace supplied
120,961,104 fetched instructions, and the complete 120M window was simulated
with Scarab's Golden Cove configuration. The raw-slot result is 0.68% frontend,
0.66% bad speculation, 21.69% retiring, and 76.97% backend bound. Evidence and
the reproducible plot are under `postgres-tpch-sf10-q7-20260710`.

## Slack-ready update

> I expanded the native database screening and found stable candidates above
> 30%: MySQL TPC-H SF10 Q18 is 32.10% backend stalled, and a MongoDB analytical
> sort over the 10M-record YCSB dataset is 36.26%. Both passed two-run stability
> checks with zero errors. I have not collected DynamoRIO traces yet; may I use
> these two application+input configurations for the final Scarab runs?
