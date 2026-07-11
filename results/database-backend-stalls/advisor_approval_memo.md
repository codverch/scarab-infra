# Database characterization decision memo

## Decision requested

The approved native screening matrix is complete, but neither database has a
stable candidate at or above 30% native backend stalls. Please confirm whether
to expand the matrix. No DynamoRIO trace has been collected.

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

## PostgreSQL audit

PostgreSQL+TPC-H remains provisionally claimed but unverified. No local artifact
currently supplies its query/scale configuration, trace provenance, SimPoint
weights, raw top-down counters, or weighted calculation. It should not be
marked complete until those files are provided and audited.

## Slack-ready update

> I completed the native MySQL and MongoDB screening matrix. The best stable
> results were MySQL TPC-C 100w/32t at 14.41% backend stalls and MongoDB YCSB A
> at 10.42%, so neither reached our 30% threshold. YCSB E was invalid because
> its measured inserts collided with warmup inserts. I have not collected any
> traces; should I expand the configuration matrix or stop these databases?
