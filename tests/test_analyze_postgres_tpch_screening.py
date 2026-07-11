import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "analyze_postgres_tpch_screening.py"
SPEC = importlib.util.spec_from_file_location("analyze_postgres_tpch_screening", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_run(
    root: Path,
    query: int,
    repetition: int,
    duration: float,
    stalls: int,
    scale_factor: float = 10,
    instructions: int = 2000000,
) -> None:
    workload = f"postgres_tpch_sf{scale_factor:g}_q{query}"
    run = root / workload / f"run{repetition}"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({
        "workload": workload,
        "system": "postgresql",
        "scale_factor": scale_factor,
        "query": query,
        "repetition": repetition,
    }))
    (run / "duration-seconds.txt").write_text(f"{duration}\n")
    (run / "exit-status.txt").write_text("0\n")
    (run / "perf.csv").write_text(
        "1000000,,cycles,1,100,,\n"
        f"{instructions},,instructions,1,100,,\n"
        f"{stalls},,stalled-cycles-backend,1,100,,\n"
        "2000,,cache-misses,1,100,,\n"
        "400000,,branches,1,100,,\n"
        "1000,,branch-misses,1,100,,\n"
    )
    (run / "mpstat.log").write_text(
        "Average: 0 1.00 0.00 2.00 0.00 0.00 0.00 0.00 0.00 0.00 97.00\n"
    )


class AnalyzePostgresTpchScreeningTest(unittest.TestCase):
    def test_selects_stable_high_backend_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, 1, 1, 10.0, 300000)
            write_run(root, 1, 2, 10.2, 320000)
            write_run(root, 2, 1, 10.0, 400000)
            write_run(root, 2, 2, 12.0, 420000)
            summaries = MODULE.summarize(MODULE.load_runs(root))
            by_query = {int(row["query"]): row for row in summaries}
            self.assertTrue(by_query[1]["eligible"])
            self.assertEqual(by_query[1]["median_backend_stall_pct"], 31.0)
            self.assertFalse(by_query[2]["eligible"])

    def test_rejects_unstable_instruction_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, 1, 1, 10.0, 400000, instructions=1000000)
            write_run(root, 1, 2, 10.0, 400000, instructions=2000000)
            summary = MODULE.summarize(MODULE.load_runs(root))[0]
            self.assertFalse(summary["eligible"])
            self.assertGreater(summary["instruction_cv_pct"], 5.0)

    def test_scale_factor_can_be_filtered_before_summarizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, 1, 1, 1.0, 900000, scale_factor=0.01)
            write_run(root, 1, 2, 1.0, 900000, scale_factor=0.01)
            write_run(root, 2, 1, 10.0, 300000)
            write_run(root, 2, 2, 10.0, 300000)
            rows = [
                row for row in MODULE.load_runs(root)
                if float(row["scale_factor"]) == 10
            ]
            summaries = MODULE.summarize(rows)
            self.assertEqual([row["query"] for row in summaries], [2])


if __name__ == "__main__":
    unittest.main()
