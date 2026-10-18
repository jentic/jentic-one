"""Tests for tools/secrets_baseline.py — the slim ``.secrets.baseline`` normalizer."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tools import secrets_baseline as sb


def _entry(filename: str, hashed: str, kind: str, line: int | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": kind,
        "filename": filename,
        "hashed_secret": hashed,
        "is_verified": False,
    }
    if line is not None:
        entry["line_number"] = line
    return entry


def _full_baseline() -> dict[str, Any]:
    """A baseline as detect-secrets writes it: line numbers, timestamp, line order."""
    return {
        "version": "1.5.0",
        "plugins_used": [{"name": "KeywordDetector"}],
        "filters_used": [],
        "results": {
            "z.py": [_entry("z.py", "h2", "Secret Keyword", 40)],
            "a.py": [
                _entry("a.py", "h9", "Secret Keyword", 3),
                _entry("a.py", "h1", "Hex High Entropy String", 12),
            ],
        },
        "generated_at": "2026-01-01T00:00:00Z",
    }


def test_normalize_strips_volatile_fields_and_sorts() -> None:
    """Normalizing drops line numbers and the timestamp and orders files and entries."""
    out = sb.normalize(_full_baseline())

    assert "generated_at" not in out
    assert list(out["results"]) == ["a.py", "z.py"]
    assert [e["hashed_secret"] for e in out["results"]["a.py"]] == ["h1", "h9"]
    assert all("line_number" not in e for es in out["results"].values() for e in es)
    assert out["version"] == "1.5.0"
    assert out["plugins_used"] == [{"name": "KeywordDetector"}]
    assert sb.finding_keys(out) == sb.finding_keys(_full_baseline())


def test_normalize_text_is_idempotent() -> None:
    """A normalized baseline normalizes to itself byte-for-byte."""
    once = sb.normalize_text(json.dumps(_full_baseline()))
    assert sb.normalize_text(once) == once
    assert once.endswith("}\n")


def test_compare_matching_slim_baseline_has_no_problems() -> None:
    """A committed slim baseline equal to the scan (whatever its line numbers) passes."""
    committed = sb.normalize_text(json.dumps(_full_baseline()))
    shifted = _full_baseline()
    shifted["results"]["a.py"][0]["line_number"] = 300
    assert sb.compare(committed, json.dumps(shifted)) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda d: d["results"]["a.py"].append(_entry("a.py", "h5", "Secret Keyword", 7)),
            "potential secret not in baseline: a.py (Secret Keyword)",
        ),
        (
            lambda d: d["results"].pop("z.py"),
            "stale baseline entry (no longer found): z.py (Secret Keyword)",
        ),
    ],
)
def test_compare_reports_new_and_stale_findings(mutate: Any, expected: str) -> None:
    """A scan with an extra or a missing finding fails with a specific message."""
    committed = sb.normalize_text(json.dumps(_full_baseline()))
    scanned = _full_baseline()
    mutate(scanned)
    assert sb.compare(committed, json.dumps(scanned)) == [expected]


def test_compare_rejects_non_slim_committed_baseline() -> None:
    """A committed baseline still carrying line numbers or a timestamp fails."""
    committed = sb.render(_full_baseline())
    problems = sb.compare(committed, json.dumps(_full_baseline()))
    assert len(problems) == 1
    assert "not in normalized slim form" in problems[0]


def test_committed_baseline_is_slim() -> None:
    """The repo's own baseline is stored in normalized slim form."""
    text = sb.BASELINE.read_text(encoding="utf-8")
    assert sb.normalize_text(text) == text
