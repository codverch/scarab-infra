# Native screening evidence

This directory is the concise audit package copied from
`amd162.utah.cloudlab.us`. It includes raw perf counters, mpstat output,
process exit status, run manifests, BenchBase summaries, software pins, and the
analyzer outputs.

The complete YCSB E workload logs are retained on the node because each is
approximately 226 MB of repeated insert-error output. For local analyzer
replay, each E `workload.log` contains the exact final 80 lines from the source
log, including throughput, successful scans, and failed insert counts. The same
tails are duplicated under `validation/` for visibility.

`SHA256SUMS` covers every evidence file except itself. The transport archive
hash recorded in `TRANSPORT_SHA256.txt` was verified before extraction.
