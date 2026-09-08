"""Unit tests for the shared trace-id guards (#903).

``emit_event`` raises ``ValueError`` on a malformed ``trace_id`` by contract;
these helpers are what emit sites and payload rebuilds sanitise through.
"""

from __future__ import annotations

import re

import pytest

from jentic_one.shared.events import (
    mint_trace_id,
    valid_trace_id_or_minted,
    valid_trace_id_or_none,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_valid_trace_id_passes_through() -> None:
    assert valid_trace_id_or_none("ab" * 16) == "ab" * 16


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "",
        "unknown",
        "AB" * 16,  # uppercase — callers normalise before validating
        "a" * 31,
        "a" * 33,
        "00-" + "a" * 32 + "-" + "b" * 16 + "-01",  # raw W3C traceparent value
        "0" * 32,  # all-zeros is invalid per W3C
    ],
)
def test_invalid_trace_id_degrades_to_none(invalid: str | None) -> None:
    assert valid_trace_id_or_none(invalid) is None


def test_mint_trace_id_is_valid_and_random() -> None:
    first, second = mint_trace_id(), mint_trace_id()
    assert _HEX32.match(first)
    assert _HEX32.match(second)
    assert first != second


def test_valid_trace_id_or_minted_keeps_a_valid_id() -> None:
    assert valid_trace_id_or_minted("cd" * 16) == "cd" * 16


def test_valid_trace_id_or_minted_replaces_garbage() -> None:
    """Payload rebuilds must never carry the literal "unknown" forward (#903)."""
    minted = valid_trace_id_or_minted("unknown")
    assert _HEX32.match(minted)
    assert minted != "unknown"
