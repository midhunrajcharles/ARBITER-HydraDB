"""Helpers for reading benchmark evaluation artifacts."""

import json
from pathlib import Path


def load_benchmark_report(report_path):
    report_data = Path(report_path).read_text(encoding="utf-8")
    return json.loads(report_data)