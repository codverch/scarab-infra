#!/usr/bin/env python3
"""Distill pass-1 ideal-fusion candidate CSVs into frequency-thresholded PGO candidates.

Pass-1 writes one row per *dynamic* fused-load instance into
    <input-dir>/{workload}/{cluster_id}.csv
with the header:
    load1_pc, load1_data_addr, load1_block_offset, load1_mem_size, load1_micro_op_num,
    load2_pc, load2_data_addr, load2_block_offset, load2_mem_size, load2_micro_op_num,
    micro_op_distance

This tool groups those dynamic instances (per trace, independently) by the static key
    (load1_pc, load2_pc, offset_delta)
where offset_delta = load2_block_offset - load1_block_offset (signed), keeps groups seen
at least N times, resolves the dominant load2_mem_size within each surviving group, and
writes one row per surviving key to:
    <output-dir>/{workload}/{cluster_id}.csv
with the header:
    load1_pc,load2_pc,offset_delta,load2_mem_size,count
"""

import argparse
import os
import sys
from collections import Counter
from multiprocessing import Pool

# 0-indexed columns we care about in the input CSV.
COL_LOAD1_PC = 0
COL_LOAD1_BLOCK_OFFSET = 2
COL_LOAD2_PC = 5
COL_LOAD2_BLOCK_OFFSET = 7
COL_LOAD2_MEM_SIZE = 8

OUTPUT_HEADER = "load1_pc,load2_pc,offset_delta,load2_mem_size\n"


def process_trace(task):
    """Aggregate a single trace CSV into thresholded PGO candidates.

    task = (input_path, output_path, threshold)
    Returns (input_path, output_path, rows_read, candidates_written).
    """
    input_path, output_path, threshold = task

    # key = (load1_pc, load2_pc, offset_delta) -> Counter of load2_mem_size.
    # Memory is bounded by the number of *static* pairs, not the dynamic row count.
    groups = {}
    rows_read = 0

    with open(input_path, "r") as fh:
        header = fh.readline()  # discard header line
        if not header:
            _write_output(output_path, [])
            return (input_path, output_path, 0, 0)

        for line in fh:
            # Manual split is much faster than the csv module for these wide,
            # simple, millions-of-rows files.
            parts = line.split(",")
            if len(parts) < COL_LOAD2_MEM_SIZE + 1:
                continue
            rows_read += 1

            load1_pc = parts[COL_LOAD1_PC]
            load2_pc = parts[COL_LOAD2_PC]
            try:
                offset_delta = int(parts[COL_LOAD2_BLOCK_OFFSET]) - int(
                    parts[COL_LOAD1_BLOCK_OFFSET]
                )
                mem_size = int(parts[COL_LOAD2_MEM_SIZE])
            except ValueError:
                continue

            key = (load1_pc, load2_pc, offset_delta)
            counter = groups.get(key)
            if counter is None:
                counter = Counter()
                groups[key] = counter
            counter[mem_size] += 1

    # Filter by threshold on the (pc1, pc2, offset_delta) group total, then pick the
    # dominant load2_mem_size. Tie-break: higher count, then smaller mem_size.
    out_rows = []
    for (load1_pc, load2_pc, offset_delta), counter in groups.items():
        total = sum(counter.values())
        if total < threshold:
            continue
        dominant_mem_size = min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        out_rows.append((load1_pc, load2_pc, offset_delta, dominant_mem_size, total))

    # Sort by count descending (stable secondary order for determinism).
    out_rows.sort(key=lambda r: (-r[4], r[0], r[1], r[2]))

    _write_output(output_path, out_rows)
    return (input_path, output_path, rows_read, len(out_rows))


def _write_output(output_path, rows):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as out:
        out.write(OUTPUT_HEADER)
        for load1_pc, load2_pc, offset_delta, mem_size, _count in rows:
            out.write(f"{load1_pc},{load2_pc},{offset_delta},{mem_size}\n")


def discover_tasks(input_dir, output_dir, threshold):
    """Find all {workload}/{cluster_id}.csv inputs and map to mirrored outputs."""
    tasks = []
    for workload in sorted(os.listdir(input_dir)):
        wl_dir = os.path.join(input_dir, workload)
        if not os.path.isdir(wl_dir):
            continue
        for name in sorted(os.listdir(wl_dir)):
            if not name.endswith(".csv"):
                continue
            input_path = os.path.join(wl_dir, name)
            output_path = os.path.join(output_dir, workload, name)
            tasks.append((input_path, output_path, threshold))
    return tasks


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate frequency-thresholded PGO fusion candidates from "
        "pass-1 ideal-fusion candidate CSVs."
    )
    parser.add_argument(
        "--input-dir",
        default="/dev/shm/baseline/ideal_fusion_candidates",
        help="Directory containing {workload}/{cluster_id}.csv pass-1 candidates.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to "
        "<input-dir>/../pgo-candidates-frequency-<N>.",
    )
    parser.add_argument(
        "--threshold",
        "-N",
        type=int,
        default=1000,
        help="Minimum occurrences of a (pc1, pc2, offset_delta) group to keep.",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=os.cpu_count(),
        help="Number of parallel worker processes.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not os.path.isdir(args.input_dir):
        sys.exit(f"error: input dir does not exist: {args.input_dir}")

    output_dir = args.output_dir
    if output_dir is None:
        parent = os.path.dirname(os.path.normpath(args.input_dir))
        output_dir = os.path.join(
            parent, f"pgo-candidates-frequency-{args.threshold}"
        )

    tasks = discover_tasks(args.input_dir, output_dir, args.threshold)
    if not tasks:
        sys.exit(f"error: no *.csv traces found under {args.input_dir}")

    jobs = max(1, min(args.jobs or 1, len(tasks)))
    print(
        f"Processing {len(tasks)} trace(s) from {args.input_dir}\n"
        f"  threshold: >= {args.threshold}\n"
        f"  output:    {output_dir}\n"
        f"  jobs:      {jobs}",
        flush=True,
    )

    total_rows = 0
    total_candidates = 0
    if jobs == 1:
        results = map(process_trace, tasks)
    else:
        pool = Pool(processes=jobs)
        results = pool.imap_unordered(process_trace, tasks)

    for input_path, output_path, rows_read, candidates in results:
        total_rows += rows_read
        total_candidates += candidates
        rel = os.path.relpath(input_path, args.input_dir)
        print(
            f"  {rel}: {rows_read:,} rows -> {candidates:,} candidates",
            flush=True,
        )

    if jobs != 1:
        pool.close()
        pool.join()

    print(
        f"Done. {total_rows:,} dynamic rows -> {total_candidates:,} static candidates "
        f"across {len(tasks)} trace(s).\nWrote: {output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
