"""Tests for the access-duration parser shared by the MCP mount."""

from __future__ import annotations

import pytest

from jentic_one.shared.auth.durations import parse_duration_seconds


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("7d", 604_800),
        ("8h", 28_800),
        ("30m", 1_800),
        ("45s", 45),
        ("3600", 3_600),
        (" 2D ", 172_800),
    ],
)
def test_parses_fixed_units(text: str, seconds: int) -> None:
    assert parse_duration_seconds(text) == seconds


@pytest.mark.parametrize("text", ["", "0d", "1w", "1.5d", "-1h", "d", "1 month"])
def test_refuses_anything_else(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration_seconds(text)
