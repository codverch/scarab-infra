"""Register the ISCA 2027 (I-Fuse) simpoint traces and pin every simulation window.

The released trace packages (HF ``deepanjalimishra99/hpca2027-final-traces`` and
``deepanjalimishra99/hpca2027-ifuse``) do not share one layout:

* prefix zips      appworld, duckdb, leveldb, terminal_bench/5: chunks 0..S
* sparse zips      bfs, dfs, pagerank, core_bench, terminal_bench/12:
                   chunk 0 (header) + the chunks just before S + chunk S
* re-cut captures  memcached, cachebench, clickhouse: chunks renumbered from 0,
                   the segment's offset inside the zip is stated by the manifest

so scarab-infra's ``cluster_id * segment_size`` arithmetic cannot place the
window. This tool states each window explicitly instead.

Window rule (the same for every simpoint):
    measured = the first MEASURE (10M) instructions of the selected segment S,
               or all of S if the trace ends sooner;
    warmup   = the WARMUP (20M) instructions that precede S in the original
               program, contiguous; less only when the package holds less.
Offsets are fetched-instruction ordinals inside the zip as counted by
DynamoRIO ``basic_counts`` and consumed by ``skip_instructions`` (what Scarab's
``--memtrace_roi_begin`` calls), so the .roi file is in Scarab's own units.

Outputs, per workload, under <traces_dir>/<suite>/<subsuite>/<workload>/traces/simp/:
    <cluster_id>.zip   hard link to the released zip (no copy)
    <cluster_id>.roi   ROI_BEGIN / WARMUP / MEASURE for run_memtrace_single_simpoint.sh
and an ``explicit_roi`` entry in workloads/workloads_db.json, plus an audit
JSON listing every window in original-program coordinates.
"""

import argparse
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

SUITE = "isca2027"
SUBSUITE = "datacenter"
IMAGE_NAME = "allbench_traces"


def dr_count(launcher, zip_path, skip=0):
    """Fetched instructions basic_counts sees in zip_path after skipping `skip`."""
    cmd = [launcher, "-simulator_type", "basic_counts", "-infile", str(zip_path),
           "-core_sharded", "false"]
    if skip:
        cmd += ["-skip_instrs", str(skip)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout + ""
    m = re.search(r"^\s*(\d+) total \(fetched\) instructions", out, re.M)
    return int(m.group(1)) if m else None


def dr_chunk_instr_count(launcher, zip_path):
    p = subprocess.Popen([launcher, "-simulator_type", "view", "-sim_refs", "20", "-infile",
                          str(zip_path), "-core_sharded", "false"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    cic = None
    for line in p.stdout:
        m = re.search(r"chunk instruction count (\d+)", line)
        if m:
            cic = int(m.group(1))
            break
    p.kill()
    p.wait()
    return cic


def read_pairs(path):
    """opt.p / opt.w lines are '<value> <cluster_id>'."""
    out = {}
    for line in Path(path).read_text().split("\n"):
        parts = line.split()
        if len(parts) == 2:
            out[int(parts[1])] = parts[0]
    return out


def chunk_index(name):
    m = re.fullmatch(r"chunk\.(\d+)", name)
    return int(m.group(1)) if m else None


def plan_workload(app_dir, launcher, warmup, measure, facts_cache):
    """Return the simpoint plans for one released package."""
    seg_size = None
    info = json.loads((app_dir / "trace_clustering_info.json").read_text()) \
        if (app_dir / "trace_clustering_info.json").exists() else {}
    if (app_dir / "fingerprint" / "segment_size").exists():
        seg_size = int((app_dir / "fingerprint" / "segment_size").read_text().split()[0])
    seg_size = seg_size or info.get("segment_size") or info.get("chunk_instruction_count")
    manifest = json.loads((app_dir / "RELEASE_MANIFEST.json").read_text()) \
        if (app_dir / "RELEASE_MANIFEST.json").exists() else {}
    by_cluster_manifest = {int(s["cluster"]): s for s in manifest.get("simpoints", [])}

    segs = read_pairs(app_dir / "simpoints" / "opt.p.lpt0.99")
    weights = read_pairs(app_dir / "simpoints" / "opt.w.lpt0.99")
    wsum = sum(float(weights[c]) for c in segs)

    plans = []
    for cluster, seg_s in sorted(segs.items()):
        seg = int(seg_s)
        ms = by_cluster_manifest.get(cluster)
        if ms is not None:
            seg = int(ms["segment"])
            zip_path = app_dir / ms["trace_file"]
        else:
            zip_path = app_dir / "traces_simp" / "trace" / f"{seg}.zip"
        assert zip_path.is_file(), f"{zip_path} missing"
        names = zipfile.ZipFile(zip_path).namelist()
        key = str(zip_path)
        if key not in facts_cache:
            facts_cache[key] = {"chunk_instr_count": dr_chunk_instr_count(launcher, zip_path),
                                "total": dr_count(launcher, zip_path)}
        cic, total = facts_cache[key]["chunk_instr_count"], facts_cache[key]["total"]

        if ms is not None and "warmup_instrs" in ms:            # memcached, cachebench
            seg_off = int(ms["warmup_instrs"])
            how = "manifest warmup_instrs"
            preceding = seg_off                                  # contiguous capture
        elif ms is not None and "trace_measurement_interval" in ms:  # clickhouse
            seg_off = int(ms["trace_measurement_interval"][0])
            how = "manifest trace_measurement_interval"
            preceding = seg_off
        else:
            assert cic == seg_size, f"{zip_path}: chunk {cic} != segment {seg_size}"
            idx = [chunk_index(n) for n in names]
            p = idx.index(seg)
            seg_off = p * cic
            how = f"entry chunk.{seg:04d} is #{p} of {len(names)}, chunk_instr_count {cic}"
            # contiguous preceding chunks: S-1, S-2, ... must sit right before p
            k = 0
            while p - k - 1 >= 0 and idx[p - k - 1] == seg - k - 1:
                k += 1
            preceding = k * cic
        if total is not None:
            avail_after = total - seg_off
        else:                                                    # DR cannot read it (clickhouse)
            avail_after = int(ms["captured_fetched_instructions"]) - seg_off
        w = min(warmup, preceding)
        m = min(measure, avail_after)
        plans.append({
            "cluster_id": cluster, "segment_id": seg,
            "weight": float(weights[cluster]) / wsum,
            "weight_raw": float(weights[cluster]),
            "zip": str(zip_path), "zip_entries": names,
            "chunk_instr_count": cic, "zip_total_instrs": total,
            "segment_size": seg_size, "segment_offset_in_zip": seg_off,
            "offset_rule": how,
            "ROI_BEGIN": seg_off - w + 1, "WARMUP": w, "MEASURE": m,
            "program_measured": [seg * seg_size, seg * seg_size + m],
            "program_warmup": [seg * seg_size - w, seg * seg_size],
        })
    return seg_size, plans


def check_distinct(plans):
    """Measured windows must be pairwise disjoint; report warmup overlaps too."""
    notes = []
    for a in plans:
        for b in plans:
            if a is b:
                continue
            am, bm, aw = a["program_measured"], b["program_measured"], a["program_warmup"]
            if a["cluster_id"] < b["cluster_id"] and am[0] < bm[1] and bm[0] < am[1]:
                raise SystemExit(f"measured windows overlap: {a['segment_id']} {b['segment_id']}")
            if aw[0] < bm[1] and bm[0] < aw[1]:
                notes.append(f"warmup of segment {a['segment_id']} covers measured "
                             f"region of segment {b['segment_id']}")
    return notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release-dir", required=True, type=Path)
    ap.add_argument("--traces-dir", required=True, type=Path)
    ap.add_argument("--dr-launcher", required=True)
    ap.add_argument("--workloads", nargs="+", required=True)
    ap.add_argument("--warmup", type=int, default=20_000_000)
    ap.add_argument("--measure", type=int, default=10_000_000)
    ap.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "workloads" / "workloads_db.json")
    ap.add_argument("--audit", required=True, type=Path)
    ap.add_argument("--facts-cache", type=Path)
    args = ap.parse_args()

    facts = json.loads(args.facts_cache.read_text()) if args.facts_cache and args.facts_cache.exists() else {}
    db = json.loads(args.db.read_text())
    audit = {"rule": {"warmup": args.warmup, "measure": args.measure}, "workloads": {}}
    for wl in args.workloads:
        seg_size, plans = plan_workload(args.release_dir / wl, args.dr_launcher,
                                        args.warmup, args.measure, facts)
        if args.facts_cache:
            args.facts_cache.write_text(json.dumps(facts, indent=1))
        notes = check_distinct(plans)
        # Offsets from chunk arithmetic are exact only if every chunk but the last
        # holds exactly chunk_instr_count instructions (DynamoRIO only cuts a chunk
        # there). basic_counts totals prove it: (n-1)*cic < total <= n*cic.
        # (-skip_instrs cannot be checked offline: basic_counts ignores it and
        # view restarts its instr# column after the skip.)
        for p in plans:
            n, cic, tot = len(p["zip_entries"]), p["chunk_instr_count"], p["zip_total_instrs"]
            if p["offset_rule"].startswith("entry"):
                assert (n - 1) * cic < tot <= n * cic, (wl, p["segment_id"], n, cic, tot)
                p["chunk_check"] = f"(n-1)*cic={(n-1)*cic} < total={tot} <= n*cic={n*cic}"
            if tot is not None:
                assert p["ROI_BEGIN"] - 1 + p["WARMUP"] + p["MEASURE"] <= tot, (wl, p["segment_id"])
        out = args.traces_dir / SUITE / SUBSUITE / wl / "traces" / "simp"
        out.mkdir(parents=True, exist_ok=True)
        for p in plans:
            dst = out / f"{p['cluster_id']}.zip"
            if dst.exists():
                dst.unlink()
            os.link(p["zip"], dst)
            (out / f"{p['cluster_id']}.roi").write_text(
                f"ROI_BEGIN={p['ROI_BEGIN']}\nWARMUP={p['WARMUP']}\nMEASURE={p['MEASURE']}\n"
                f"# segment {p['segment_id']} ({p['offset_rule']})\n")
        db.setdefault(SUITE, {}).setdefault(SUBSUITE, {})[wl] = {
            "simulation": {"prioritized_mode": "memtrace", "memtrace": {
                "image_name": IMAGE_NAME, "segment_size": seg_size, "warmup": args.warmup,
                "trace_type": "explicit_roi", "whole_trace_file": None}},
            "simpoints": [{"cluster_id": p["cluster_id"], "segment_id": p["segment_id"],
                           "weight": p["weight"]} for p in plans],
        }
        audit["workloads"][wl] = {"segment_size": seg_size, "simpoints": plans, "notes": notes}
        print(f"{wl}: " + ", ".join(f"seg {p['segment_id']} roi {p['ROI_BEGIN']} w {p['WARMUP']} m {p['MEASURE']}"
                                     for p in plans))
    args.db.write_text(json.dumps(db, indent=2) + "\n")
    args.audit.write_text(json.dumps(audit, indent=1) + "\n")


if __name__ == "__main__":
    main()
