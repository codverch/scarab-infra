# HPCA 2027 DCPerf Characterization Status

Date: 2026-07-09

This document separates software setup, native workload validation, trace
validation, and final characterization. A successful installer is not counted
as a completed workload result.

## Fixed Inputs

- Node: `Harry123@amd162.utah.cloudlab.us`
- DCPerf commit: `4dc3b5e8836796fb7d80316f43a1147d052dc2e7`
- Scarab branch/commit: `hpca2027-characterization` / `7185dea5`
- scarab-infra branch/commit at setup: `hpca2027-baseline` / `987e72a`
- Trace collection: pinned DynamoRIO `trace_for_instrs`, workload-calibrated
  delayed windows, bounded 100,000,000-instruction collection, and
  10,000,000-instruction trace chunks. The stale infra-only
  `count_fetched_instrs` option is not supported by this pinned DynamoRIO and
  is not used.

## Agentic Prerequisite

The agentic portion is complete. The graph contains `chemcrow`,
`langchain_web`, `rag_haystack`, `swe_agent`, and `toolformer`; all 15 SimPoint
runs were validated, their weights normalize to one per workload, and the
results/config/plot were pushed in scarab-infra commit `987e72a`.

## DCPerf Native Validation

| Workload | Setup | Native validation | Trace state |
| --- | --- | --- | --- |
| MediaWiki | Complete, HHVM 3.30.12 | Official one-minute native run: 77,909 requests, 74,659 successful, 1,296.33 requests/s | Blocked: live HHVM attach crashes; launch-mode trace is not steady-state |
| FeedSim | Complete | Fixed-QPS smoke: 9.67 achieved QPS, 305.62 ms p95, one successful instance | Full aligned 100M trace validated |
| TaoBench | Complete with two reviewable installer patches | TLS server/client ran successfully with positive fast/slow QPS and request operations | Full execution-phase 100M trace validated |
| Django | Complete | 30 s standalone smoke: 26,358 successful, zero failed, 884.79 transactions/s | Blocked: direct attach to a uWSGI worker produced empty metadata/raw files |
| VideoTranscode | Binaries complete | Blocked: the recommended El Fuente Y4M clips require CDVL registration and are intentionally not downloaded by DCPerf | Blocked on official dataset |
| Spark | Spark 2.4.5 and Java 8 complete | Deferred by Harry on 2026-07-09 | Deferred; do not run for now |

The Django smoke emitted a database-flush warning before schema setup, then
served the full request mix successfully with zero transaction failures. Keep
the warning in the provenance record and repeat a longer validation before the
final trace.

The accepted FeedSim pilot uses a calibrated 350B-instruction delay followed by
a 1M-instruction trace under a 0.02-QPS client. The client window began at
18:23:53, its first dispatch was due at approximately 18:24:43, and the first
raw trace write occurred at 18:25:36, proving that the trace followed request
dispatch rather than initialization. Thirty requests completed. Conversion
produced 99 valid per-thread trace ZIPs plus CPU and serial schedules, and
`basic_counts` reported 1,242,420 fetched instructions. The accepted output is
`traces/feedsim_alignment_1m_delay350b_20260709` on the Utah node. Earlier
100M-300B pilots are calibration artifacts and are not characterization data.

The accepted full FeedSim trace is
`traces/feedsim_steady_100m_delay350b_20260709`. Its client measurement began
at 18:32:00, the first 0.02-QPS dispatch was due at approximately 18:32:50,
and raw trace writes began at 18:33:42. Thirty requests completed. The trace
contains 99 valid per-thread ZIPs, CPU and serial schedules, and 100,267,218
fetched instructions according to `basic_counts`. Its total size is 147 MB.

A 1M-instruction Scarab top-down smoke also completed successfully from the
largest converted FeedSim thread ZIP, matching scarab-infra's existing
`get_largest_trace` selection policy. The run retired 1M instructions in
328,222 cycles (3.05 IPC) and produced frontend bound 9.630%, bad speculation
10.575%, retiring 59.931%, and backend bound 19.863%. The raw slot counters
sum to 1,969,332 slots and the four derived categories sum to 100%. This smoke
validates trace readability and statistic extraction; it is not a final
SimPoint-weighted FeedSim workload result.

MediaWiki passed a longer official native validation with the full MLP request
mix and native warmup. Launching HHVM under DynamoRIO captured initialization,
slowed the 300-request warmup past its timeout, and reported zero successful
WRK requests, so that output is rejected. A direct `libdrmemtrace.so` attach
was then aligned to the harness's `exec-after-warmup` hook: HHVM declared
itself warm at 19:24:44, attachment created the trace root 111 ms later, and
measured WRK traffic started afterward. The old JIT-enabled HHVM process then
segfaulted. That failure is preserved as evidence, but no MediaWiki trace is
counted complete.

Django also passed native validation. A 30-second standalone run completed
26,358 requests with zero failures at 884.79 transactions/s, and a later
bounded pilot completed approximately 27,178 requests with zero failures at
931.71 transactions/s. Direct `libdrmemtrace.so` attachment to one of the 32
official uWSGI workers created a trace root, but its metadata and raw files
remained empty after graceful server shutdown. That output is rejected. A
launch-mode method, or an explicitly approved single-worker characterization,
is still required.

The accepted full TaoBench trace is
`traces/tao_server_execphase_100m_delay65b_20260709`. Tao drops privileges to
`nobody`, so DynamoRIO trace files are staged on node-local `/tmp`, converted
and validated there, and moved to `/proj` only after validation. This avoids
the observed NFS permission failure without changing the server, TLS, cache,
thread, or client configuration. Calibration established that 55B instructions
still activated 1.34 seconds before the execution phase; the accepted 65B
delay activated at 20:14:47.535, 0.309 seconds after the harness entered its
execution phase and 37.83 seconds before client completion. The final package
contains 109 valid per-thread ZIPs, no ZIP errors, and 100,930,247 fetched
instructions according to `basic_counts`. Its total size is 59 MB.

A 1M-instruction Scarab top-down smoke completed from the largest converted
TaoBench server-thread ZIP using Scarab commit `7185dea` and the same
`PARAMS.in` (`--dcache_assoc 8`). It retired 1M instructions in 298,132 cycles
(3.35 IPC). The raw slot counters produce frontend bound 1.032%, bad
speculation 0.352%, retiring 70.359%, and backend bound 28.257%; the categories
sum to 100% and backend bound is non-negative. This validates trace readability
and top-down extraction, but it is not a final multi-thread-weighted TaoBench
result.

## Trace Targets

- MediaWiki: HHVM server process, not `wrk` or the Benchpress launcher.
- FeedSim: `LeafNodeRank`, with `DriverNodeRank` used only as the load generator.
- TaoBench: `tao_bench_server`, with the private dependency directory in
  `LD_LIBRARY_PATH` so both `libssl.so.3` and `libcrypto.so.3` come from the
  pinned build.
- Django: uWSGI application workers, not Siege or Cassandra.
- VideoTranscode: pinned static ffmpeg/SVT-AV1 worker after the official clips
  are installed.
- Spark: Spark executor JVM during the compute-intensive stage, only after the
  dataset and storage configuration pass the repository's I/O requirements.

## Remaining Execution Order

1. Develop launch-mode tracing that preserves the official MediaWiki and
   Django workload behavior; do not count attach crashes or empty traces.
2. Obtain the registered El Fuente dataset for VideoTranscode. Spark is
   explicitly deferred and must not be started until Harry changes priority.
3. Add validated FeedSim and TaoBench results to the existing agentic/top-down
   plot, then add MediaWiki, Django, and VideoTranscode only after their traces
   pass the same validation.
4. Leave PostgreSQL+TPC-H and alternate MongoDB/MySQL configurations until the
   DCPerf characterization is closed, as requested.

## Reproducibility Files

- `scripts/bootstrap_dcperf_cloudlab.sh`
- `scripts/install_dcperf_mediawiki_hhvm.sh`
- `scripts/run_dcperf_feedsim_trace_pilot.sh`
- `scripts/run_dcperf_mediawiki_trace_pilot.sh`
- `scripts/run_dcperf_django_trace_pilot.sh`
- `scripts/run_dcperf_tao_trace_pilot.sh`
- `workloads/dcperf/patches/tao_bench_ubuntu_libcrypto.patch`
- `workloads/dcperf/patches/tao_bench_memcached_download.patch`
