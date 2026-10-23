"""Unit tests for the ``Retry-After`` normalizer (§09 E4.1).

The broker collapses an upstream's heterogeneous rate-limit signal — bare
``Retry-After`` seconds, an RFC 7231 ``Retry-After`` HTTP-date, or a
``X-RateLimit-Reset`` unix epoch — into a single non-negative integer of seconds
the agent can act on uniformly. Resolution order: Retry-After seconds ->
Retry-After date - now -> X-RateLimit-Reset - now -> ``None``. Clamped >= 0 and
capped at the deadline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime

from jentic_one.broker.core.retry_after import parse_retry_after_seconds

# A fixed "now" (2021-01-01T00:00:00Z) so date math is deterministic.
_NOW = 1_609_459_200.0


def test_no_signal_returns_none() -> None:
    assert parse_retry_after_seconds({}, now_epoch=_NOW) is None


def test_retry_after_seconds() -> None:
    assert parse_retry_after_seconds({"Retry-After": "30"}, now_epoch=_NOW) == 30


def test_retry_after_seconds_case_insensitive() -> None:
    assert parse_retry_after_seconds({"retry-after": "12"}, now_epoch=_NOW) == 12


def test_retry_after_http_date() -> None:
    future = datetime.fromtimestamp(_NOW + 45, tz=UTC)
    headers = {"Retry-After": format_datetime(future, usegmt=True)}
    assert parse_retry_after_seconds(headers, now_epoch=_NOW) == 45


def test_retry_after_past_date_clamped_to_zero() -> None:
    past = datetime.fromtimestamp(_NOW - 60, tz=UTC)
    headers = {"Retry-After": format_datetime(past, usegmt=True)}
    assert parse_retry_after_seconds(headers, now_epoch=_NOW) == 0


def test_x_ratelimit_reset_epoch() -> None:
    headers = {"X-RateLimit-Reset": str(int(_NOW + 90))}
    assert parse_retry_after_seconds(headers, now_epoch=_NOW) == 90


def test_retry_after_takes_precedence_over_reset() -> None:
    headers = {"Retry-After": "10", "X-RateLimit-Reset": str(int(_NOW + 999))}
    assert parse_retry_after_seconds(headers, now_epoch=_NOW) == 10


def test_capped_at_deadline() -> None:
    assert parse_retry_after_seconds({"Retry-After": "300"}, now_epoch=_NOW, cap_s=20) == 20


def test_garbage_retry_after_falls_through_to_reset() -> None:
    headers = {"Retry-After": "soon", "X-RateLimit-Reset": str(int(_NOW + 15))}
    assert parse_retry_after_seconds(headers, now_epoch=_NOW) == 15


def test_garbage_everything_returns_none() -> None:
    headers = {"Retry-After": "soon", "X-RateLimit-Reset": "never"}
    assert parse_retry_after_seconds(headers, now_epoch=_NOW) is None


def test_empty_header_values_ignored() -> None:
    assert parse_retry_after_seconds({"Retry-After": "  "}, now_epoch=_NOW) is None


def test_fractional_retry_after_rounds_up() -> None:
    # The shared parser (#1957) accepts a fractional delay and rounds it up.
    assert parse_retry_after_seconds({"Retry-After": "1.5"}, now_epoch=_NOW) == 2


def test_retry_after_http_date_minus_zero_zone_is_utc() -> None:
    when = datetime.fromtimestamp(_NOW + 45, tz=UTC).strftime("%a, %d %b %Y %H:%M:%S -0000")
    assert parse_retry_after_seconds({"Retry-After": when}, now_epoch=_NOW) == 45
