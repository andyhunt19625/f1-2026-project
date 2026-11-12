import os
import tempfile
import time
import unittest
from pathlib import Path

from race_engineer import report
from tests.test_compare import build


class ReportTests(unittest.TestCase):
    def test_one_read_then_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build(p)
            first = report.run(p)
            self.assertIn("cache saved", first)
            self.assertIn("=== Your laps ===", first)
            self.assertIn("vs Verstappen", first)
            self.assertTrue((Path(d) / report.CACHE_NAME).is_file())
            second = report.run(p)
            self.assertIn("from cache", second)
            self.assertEqual(first.split("\n", 1)[1], second.split("\n", 1)[1])   # same analysis

    def test_cache_rebuilt_when_session_file_is_newer(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build(p)
            report.run(p)
            later = time.time() + 5
            os.utime(p, (later, later))
            self.assertIn("cache saved", report.run(p))


if __name__ == "__main__":
    unittest.main()
