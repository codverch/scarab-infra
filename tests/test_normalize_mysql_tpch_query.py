import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "normalize_mysql_tpch_query.py"


class NormalizeMysqlTpchQueryTest(unittest.TestCase):
    def normalize(self, text: str) -> str:
        return subprocess.run(
            [str(SCRIPT)], input=text, text=True, capture_output=True, check=True
        ).stdout

    def test_dates_and_intervals(self) -> None:
        result = self.normalize("date '1998-12-01' - interval '88 days' + interval '3' month")
        self.assertEqual(result, "'1998-12-01' - interval 88 day + interval 3 month")

    def test_substring(self) -> None:
        result = self.normalize("substring(c_phone from 1 for 2)")
        self.assertEqual(result, "substring(c_phone, 1, 2)")


if __name__ == "__main__":
    unittest.main()
