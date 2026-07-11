import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "normalize_postgres_tpch_query.py"
SPEC = importlib.util.spec_from_file_location("normalize_postgres_tpch_query", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class NormalizePostgresTpchQueryTest(unittest.TestCase):
    def test_removes_unlimited_marker_and_normalizes_interval(self) -> None:
        source = "select * from lineitem where d < date '1998-12-01' - interval '89' day (3);\nlimit -1;\n"
        self.assertEqual(
            MODULE.normalize(source),
            "select * from lineitem where d < date '1998-12-01' - interval '89 days';\n",
        )

    def test_moves_positive_limit_before_final_semicolon(self) -> None:
        source = "select *\nfrom orders\norder by 1;\nlimit 10;\n"
        self.assertEqual(
            MODULE.normalize(source),
            "select *\nfrom orders\norder by 1\nlimit 10;\n",
        )


if __name__ == "__main__":
    unittest.main()
