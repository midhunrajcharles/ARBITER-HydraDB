import json
import tempfile
import unittest
from pathlib import Path

from arbiter.benchmark import BenchmarkReportError, load_benchmark_report


class BenchmarkReportTests(unittest.TestCase):
    def test_loads_json_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            report_path.write_text(json.dumps({"summary": {"n": 1}}), encoding="utf-8")

            self.assertEqual(load_benchmark_report(report_path)["summary"]["n"], 1)

    def test_missing_report_raises_benchmark_error(self):
        with self.assertRaises(BenchmarkReportError):
            load_benchmark_report("missing-report.json")


if __name__ == "__main__":
    unittest.main()