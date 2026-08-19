"""Helpers for reading benchmark evaluation artifacts."""

import json
from pathlib import Path


class BenchmarkReportError(ValueError):
    """Raised when a benchmark report cannot be read or decoded."""


def load_benchmark_report(report_path):
    try:
        report_data = Path(report_path).read_text(encoding="utf-8")
        return json.loads(report_data)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkReportError(str(exc)) from exc