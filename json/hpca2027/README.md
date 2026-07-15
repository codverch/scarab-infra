# HPCA 2027 simulation descriptors

## Runtime I-Fuse with 10M warmup and 20M measurement

`runtime_ifuse_10m_warmup_20m_run.json` compares an I-Fuse-disabled baseline
with runtime-trained I-Fuse on `bfs`, `dfs`, `pagerank`, and `tc`. Both
configurations use the first 10M instructions for fast warmup and measure the
following 20M instructions.

The current `workloads/workloads_db.json` entries describe 30M-instruction
segments but record `warmup: 0`. Scarab-infra's validator therefore rejects a
10M descriptor warmup even though the locally available `trace_then_cluster`
archives for `bfs`, `dfs`, and `pagerank` contain multiple chunks. The `tc`
trace is not currently installed under `/dev/shm/baseline/simpoint_traces`.

Intended trace interpretation:

- first 10M instructions: fast warmup;
- next 20M instructions: measured interval; and
- 30M instructions consumed per selected trace in total.

Before launching, either record 10M as available warmup for these traces after
verifying their chunk layout, or extend Scarab-infra with an explicit
"consume ROI prefix as warmup" mode. Do not silently change global workload
metadata: it would alter the semantics of other descriptors. SimPoints at the
beginning of a trace have no preceding instructions and require either a
shorter warmup or exclusion from a uniform-warmup experiment.
