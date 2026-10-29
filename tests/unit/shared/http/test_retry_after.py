"""Unit tests for the shared ``Retry-After`` parser (#1957).

The broker's retry runner and the MCP transport-error relay both resolve
``Retry-After`` through ``parse_retry_after``, so these pin the one rule set:
delay-seconds (a fraction rounds up), an HTTP-date in ``GMT`` or ``-0000``
resolved in UTC, and a past date resolving to zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from jentic_one.shared.http.retry_after import MAX_RETRY_AFTER_S, parse_retry_after

# A fixed "now" (2021-01-01T00:00:00Z) so date math is deterministic.
_NOW = 1_609_459_200.0
_NOW_DT = datetime.fromtimestamp(_NOW, tz=UTC)


def test_integer_seconds() -> None:
    assert parse_retry_after("30", now_epoch=_NOW) == 30


def test_zero_seconds() -> None:
    assert parse_retry_after("0", now_epoch=_NOW) == 0


@pytest.mark.parametrize(("raw", "want"), [("1.5", 2), ("0.2", 1), ("3.0", 3), (" 2.01 ", 3)])
def test_fractional_seconds_round_up(raw: str, want: int) -> None:
    assert parse_retry_after(raw, now_epoch=_NOW) == want


def test_http_date_gmt() -> None:
    when = format_datetime(_NOW_DT + timedelta(seconds=45), usegmt=True)
    assert when.endswith("GMT")
    assert parse_retry_after(when, now_epoch=_NOW) == 45


@pytest.mark.usefixtures("non_utc_local_time")
def test_http_date_minus_zero_zone_is_utc_not_local_time() -> None:
    # ``-0000`` parses to a naive datetime; reading it as local time would be
    # off by the server's UTC offset (5h30 here).
    when = (_NOW_DT + timedelta(seconds=45)).strftime("%a, %d %b %Y %H:%M:%S -0000")
    assert parse_retry_after(when, now_epoch=_NOW) == 45


@pytest.mark.usefixtures("non_utc_local_time")
def test_http_date_gmt_under_non_utc_local_time() -> None:
    when = format_datetime(_NOW_DT + timedelta(seconds=45), usegmt=True)
    assert parse_retry_after(when, now_epoch=_NOW) == 45


def test_past_http_date_resolves_to_zero() -> None:
    when = format_datetime(_NOW_DT - timedelta(seconds=120), usegmt=True)
    assert parse_retry_after(when, now_epoch=_NOW) == 0


def test_http_date_defaults_to_the_current_time() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    got = parse_retry_after(when)
    assert got is not None
    assert 58 <= got <= 60


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_absent_or_blank_is_none(raw: str | None) -> None:
    assert parse_retry_after(raw, now_epoch=_NOW) is None


@pytest.mark.parametrize("raw", ["soon", "-5", "+5", "1e3", "inf", "nan", "1_0", ".5", "5.", "١٢"])
def test_not_a_delay_or_date_is_none(raw: str) -> None:
    assert parse_retry_after(raw, now_epoch=_NOW) is None


def test_large_integer_seconds_are_exact() -> None:
    # Parsed as an integer, not through ``float``, so no precision is lost.
    assert parse_retry_after("86399", now_epoch=_NOW) == 86_399
    assert parse_retry_after("00042", now_epoch=_NOW) == 42


@pytest.mark.parametrize("raw", ["86401", "9" * 300, "9" * 5000, "123456789.5"])
def test_huge_delay_is_capped(raw: str) -> None:
    assert parse_retry_after(raw, now_epoch=_NOW) == MAX_RETRY_AFTER_S


def test_far_future_http_date_is_capped() -> None:
    when = format_datetime(_NOW_DT + timedelta(days=30), usegmt=True)
    assert parse_retry_after(when, now_epoch=_NOW) == MAX_RETRY_AFTER_S
