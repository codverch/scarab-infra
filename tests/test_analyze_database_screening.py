import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "analyze_database_screening.py"
SPEC = importlib.util.spec_from_file_location("analyze_database_screening", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_run(root: Path, workload: str, repetition: int, throughput: float, stalls: int) -> None:
    run_dir = root / workload / f"run{repetition}"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"workload": workload, "system": "mongodb", "repetition": repetition})
    )
    (run_dir / "perf.csv").write_text(
        "1000000,,cycles,1,100,,\n"
        "2000000,,instructions,1,100,,\n"
        f"{stalls},,stalled-cycles-backend,1,100,,\n"
        "2000,,cache-misses,1,100,,\n"
        "400000,,branches,1,100,,\n"
        "1000,,branch-misses,1,100,,\n"
    )
    (run_dir / "workload.log").write_text(
        f"[OVERALL], Throughput(ops/sec), {throughput}\n"
        "[READ-FAILED], Operations, 0\n"
    )
    (run_dir / "mpstat.log").write_text(
        "Average: 0 1.00 0.00 2.00 0.00 0.00 0.00 0.00 0.00 0.00 97.00\n"
    )


class AnalyzeDatabaseScreeningTest(unittest.TestCase):
    def test_analytical_throughput_uses_inverse_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "duration-seconds.txt").write_text("25.0\n")

            self.assertEqual(
                MODULE.parse_throughput(run_dir, "mysql_tpch"), 0.04
            )

    def test_prefers_benchbase_summary_throughput_over_goodput(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            results = run_dir / "benchbase-results"
            results.mkdir()
            (results / "tpcc.summary.json").write_text(
                json.dumps({"Throughput (requests/second)": 387.912})
            )
            (run_dir / "workload.log").write_text(
                "387.912 requests/sec (throughput), 414.458 requests/sec (goodput)\n"
            )

            self.assertEqual(MODULE.parse_throughput(run_dir, "mysql"), 387.912)

    def test_counts_all_ycsb_failed_operation_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workload.log"
            path.write_text(
                "[INSERT], Return=ERROR, 249631\n"
                "[INSERT-FAILED], Operations, 249631\n"
                "[SCAN-FAILED], Operations, 7\n"
            )

            self.assertEqual(MODULE.parse_errors(path, "mongodb"), 249638)

    def test_selects_stable_highest_backend_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, "mongodb_ycsb_a_10m", 1, 1000.0, 300000)
            write_run(root, "mongodb_ycsb_a_10m", 2, 1020.0, 320000)
            write_run(root, "mongodb_ycsb_c_10m", 1, 900.0, 400000)
            write_run(root, "mongodb_ycsb_c_10m", 2, 1000.0, 420000)

            rows = MODULE.load_runs(root)
            summaries = MODULE.summarize(rows)
            by_name = {row["workload"]: row for row in summaries}

            self.assertTrue(by_name["mongodb_ycsb_a_10m"]["eligible"])
            self.assertEqual(
                by_name["mongodb_ycsb_a_10m"]["median_backend_stall_pct"], 31.0
            )
            self.assertFalse(by_name["mongodb_ycsb_c_10m"]["eligible"])
            self.assertGreater(
                by_name["mongodb_ycsb_c_10m"]["throughput_cv_pct"], 5.0
            )

    def test_rejects_unstable_instruction_counts(self) -> None:
        rows = [
            {
                "workload": "candidate",
                "system": "mysql_tpch",
                "throughput": 1.0,
                "backend_stall_pct": 35.0,
                "instructions": 100.0,
                "cycles": 100.0,
                "errors": 0,
                "cpu_util_pct": 50.0,
            },
            {
                "workload": "candidate",
                "system": "mysql_tpch",
                "throughput": 1.0,
                "backend_stall_pct": 36.0,
                "instructions": 200.0,
                "cycles": 100.0,
                "errors": 0,
                "cpu_util_pct": 50.0,
            },
        ]

        summary = MODULE.summarize(rows)[0]

        self.assertFalse(summary["eligible"])
        self.assertGreater(summary["instruction_cv_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
