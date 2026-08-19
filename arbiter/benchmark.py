"""Helpers for reading benchmark evaluation artifacts."""

import json
from pathlib import Path


def load_report(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))