"""Real_Samples_Index builder and checker.

Extracts all string field values from JSON files in the real-samples directory
and provides O(1) lookup to detect production data leakage in synthetic entities.
"""

import json
import os
from pathlib import Path
from typing import Any


class RealSamplesIndex:
    """Builds an index of all string values from real sample JSON files."""

    def __init__(self, samples_dir: str = "real-samples") -> None:
        self._index: set[str] = set()
        self._build_index(samples_dir)

    def _build_index(self, samples_dir: str) -> None:
        """Walk all JSON files under samples_dir and extract string values."""
        root = Path(samples_dir)
        if not root.exists():
            return
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if not filename.endswith(".json"):
                    continue
                filepath = Path(dirpath) / filename
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._extract_strings(data)
                except (json.JSONDecodeError, OSError):
                    continue

    def _extract_strings(self, obj: Any) -> None:
        """Recursively extract all string values from a JSON structure."""
        if isinstance(obj, str):
            self._index.add(obj)
        elif isinstance(obj, dict):
            for value in obj.values():
                self._extract_strings(value)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_strings(item)

    def check(self, value: str) -> bool:
        """Return True if value (length >= 4) matches any indexed entry."""
        if len(value) < 4:
            return False
        return value in self._index
