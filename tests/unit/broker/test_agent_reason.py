"""Unit tests for reading the agent's ``Jentic-Reason`` header."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from jentic_one.broker.core.reason import MAX_AGENT_REASON_LEN, parse_agent_reason


@pytest.mark.parametrize("raw", [None, "", "   ", "%20%0A", "\x00\x07"])
def test_absent_or_blank_reason_is_none(raw: str | None) -> None:
    assert parse_agent_reason(raw) is None


def test_plain_ascii_passes_through() -> None:
    assert (
        parse_agent_reason("Customer 4821 asked for a refund") == "Customer 4821 asked for a refund"
    )


def test_percent_encoded_utf8_is_decoded() -> None:
    text = "Remboursement demandé — ticket #4821 (发布)"
    assert parse_agent_reason(quote(text, safe="")) == text


def test_plus_is_literal_not_a_space() -> None:
    assert parse_agent_reason("1+1%20is%202") == "1+1 is 2"


def test_raw_utf8_header_decoded_as_latin1_is_recovered() -> None:
    # Starlette hands header bytes over as latin-1; a raw-UTF-8 sender still reads right.
    raw = "café 发布".encode().decode("latin-1")
    assert parse_agent_reason(raw) == "café 发布"


def test_controls_dropped_newlines_and_tabs_kept() -> None:
    assert (
        parse_agent_reason(quote("line one\r\nline\ttwo\x1b[31m", safe=""))
        == "line one\nline\ttwo[31m"
    )


def test_capped_at_max_length() -> None:
    out = parse_agent_reason("x" * (MAX_AGENT_REASON_LEN + 50))
    assert out is not None
    assert len(out) == MAX_AGENT_REASON_LEN


def test_malformed_percent_sequence_never_raises() -> None:
    assert parse_agent_reason("50%-off %ZZ") == "50%-off %ZZ"
