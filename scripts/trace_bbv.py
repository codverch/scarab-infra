"""Per-segment basic-block vectors straight from a drmemtrace zip, for SimPoint.

The vector of a segment is the fetched-instruction count of every PC in it,
which equals the classic size-weighted BBV spread over each block's PCs (the
two differ by a fixed linear map, which SimPoint's random projection absorbs).
Taking the vectors from the very trace that is simulated keeps segment
boundaries and window offsets in one coordinate system: fetched-instruction
ordinals, as counted by DynamoRIO basic_counts and skipped by
skip_instructions (Scarab --memtrace_roi_begin).

Records are DynamoRIO trace_entry_t {uint16 type, uint16 size, uint64 addr}.
Fetched instructions are types 10-17 (INSTR .. INSTR_BUNDLE), 31 (SYSENTER),
48/49 (TAKEN/UNTAKEN_JUMP); 29 (INSTR_NO_FETCH) is not fetched.

Writes <out>.bbfp (SimPoint -loadFVFile format, one "T" line per segment)
and <out>.json (total instructions, per-segment counts). The total must equal
basic_counts' "total (fetched) instructions"; pass --expect-total to assert it.
"""

import argparse
import json
import zipfile

import numpy as np

REC = np.dtype([("t", "<u2"), ("s", "<u2"), ("a", "<u8")])
FETCHED = np.array(list(range(10, 18)) + [31, 48, 49], dtype=np.uint16)
BUNDLE = 17


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip")
    ap.add_argument("--segment-size", type=int, default=10_000_000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-total", type=int)
    args = ap.parse_args()

    seg = args.segment_size
    pc_index = {}
    seg_counts = {}            # segment -> {pc_idx: count}
    total = 0
    bundles = 0
    with zipfile.ZipFile(args.zip) as z:
        for name in z.namelist():
            buf = z.read(name)
            assert len(buf) % REC.itemsize == 0, f"{name}: {len(buf)} not a multiple of 12"
            r = np.frombuffer(buf, dtype=REC)
            pcs = r["a"][np.isin(r["t"], FETCHED)]
            bundles += int(np.count_nonzero(r["t"] == BUNDLE))
            if pcs.size == 0:
                continue
            ords = np.arange(total, total + pcs.size, dtype=np.int64)
            segs = ords // seg
            for s in np.unique(segs):
                u, c = np.unique(pcs[segs == s], return_counts=True)
                d = seg_counts.setdefault(int(s), {})
                for pc, n in zip(u.tolist(), c.tolist()):
                    i = pc_index.setdefault(pc, len(pc_index) + 1)
                    d[i] = d.get(i, 0) + n
            total += pcs.size
    assert bundles == 0, f"{bundles} INSTR_BUNDLE records: counts would be wrong"
    if args.expect_total is not None:
        assert total == args.expect_total, f"decoded {total} != basic_counts {args.expect_total}"

    with open(args.out + ".bbfp", "w") as f:
        for s in sorted(seg_counts):
            f.write("T" + "".join(f":{i}:{n} " for i, n in sorted(seg_counts[s].items())) + "\n")
    meta = {"zip": args.zip, "segment_size": seg, "total_fetched": total,
            "segments": {s: sum(seg_counts[s].values()) for s in sorted(seg_counts)},
            "distinct_pcs": len(pc_index)}
    with open(args.out + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps({k: meta[k] for k in ("total_fetched", "distinct_pcs")}),
          "segments:", len(seg_counts))


if __name__ == "__main__":
    main()
