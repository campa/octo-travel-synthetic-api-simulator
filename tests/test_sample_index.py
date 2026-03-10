"""Tests for RealSamplesIndex."""

import json
import os
import pytest
from seeder.sample_index import RealSamplesIndex


@pytest.fixture
def sample_dir(tmp_path):
    """Create a temporary directory with sample JSON files."""
    d = tmp_path / "samples"
    d.mkdir()

    # Nested product-like JSON
    product = {
        "id": "abc123-def456",
        "internalName": "Sunset Kayak Tour",
        "options": [
            {
                "id": "opt-001",
                "internalName": "Standard Option",
                "units": [{"id": "unit-1", "type": "ADULT", "internalName": "Adult Ticket"}],
            }
        ],
        "description": "A beautiful kayak tour at sunset",
    }
    with open(d / "products.json", "w") as f:
        json.dump(product, f)

    # Calendar-like JSON (array at top level)
    calendar = [
        {"localDate": "2026-06-01", "status": "AVAILABLE", "statusMessage": "Available"},
        {"localDate": "2026-06-02", "status": "SOLD_OUT", "statusMessage": "Sold Out"},
    ]
    with open(d / "calendar.json", "w") as f:
        json.dump(calendar, f)

    return str(d)


def test_index_finds_nested_string_values(sample_dir):
    idx = RealSamplesIndex(sample_dir)
    assert idx.check("Sunset Kayak Tour") is True
    assert idx.check("Standard Option") is True
    assert idx.check("Adult Ticket") is True
    assert idx.check("abc123-def456") is True


def test_index_finds_array_string_values(sample_dir):
    idx = RealSamplesIndex(sample_dir)
    assert idx.check("AVAILABLE") is True
    assert idx.check("Available") is True
    assert idx.check("SOLD_OUT") is True
    assert idx.check("Sold Out") is True


def test_check_rejects_short_strings(sample_dir):
    """Strings shorter than 4 chars should always return False."""
    idx = RealSamplesIndex(sample_dir)
    # "id" key values like "opt-001" are >= 4, but the string "ADULT" is 5 chars
    # Short strings should return False regardless of index content
    assert idx.check("AB") is False
    assert idx.check("abc") is False
    assert idx.check("") is False


def test_check_returns_false_for_non_matching(sample_dir):
    idx = RealSamplesIndex(sample_dir)
    assert idx.check("Completely Fictional Name") is False
    assert idx.check("nonexistent-id-value") is False


def test_nonexistent_directory():
    """Index should handle missing directory gracefully."""
    idx = RealSamplesIndex("/nonexistent/path/to/samples")
    assert idx.check("anything") is False


def test_invalid_json_files_skipped(tmp_path):
    """Invalid JSON files should be skipped without error."""
    d = tmp_path / "bad_samples"
    d.mkdir()
    with open(d / "bad.json", "w") as f:
        f.write("not valid json {{{")
    with open(d / "good.json", "w") as f:
        json.dump({"name": "Valid Entry"}, f)

    idx = RealSamplesIndex(str(d))
    assert idx.check("Valid Entry") is True


def test_real_samples_directory():
    """Smoke test: index the actual real-samples directory."""
    idx = RealSamplesIndex("real-samples")
    # The real samples contain OCTO status strings
    assert idx.check("AVAILABLE") is True
    # Short strings should still be rejected
    assert idx.check("kg") is False
