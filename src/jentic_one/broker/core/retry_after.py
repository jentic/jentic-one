"""Normalize an upstream's heterogeneous rate-limit signal to integer seconds.

Upstreams say "come back later" in mutually-incompatible ways:

- ``Retry-After: <seconds>`` (Stripe and most APIs),
- ``Retry-After: <HTTP-date>`` (RFC 7231 date form),
- ``X-RateLimit-Reset: <unix-epoch>`` (GitHub et al.),

An autonomous agent is bad at parsing varied formats and doing date math, so the
broker resolves the wait to a **single non-negative integer of seconds** and
emits it uniformly: a canonical ``Retry-After: <seconds>`` header plus a
``retry_after_seconds`` field in the ``wait`` agent directive. The agent's system
prompt then needs one rule: *"on ``wait``, sleep ``retry_after_seconds``."*

Resolution order (first that yields a value wins):
``Retry-After`` seconds -> ``Retry-After`` HTTP-date - now -> ``X-RateLimit-Reset``
epoch - now -> ``None`` (caller supplies a backoff default). The result is clamped
to ``>= 0`` and capped at an optional ``cap_s`` (the remaining deadline budget).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_retry_after_seconds(
    headers: Mapping[str, str],
    *,
    now_epoch: float | None = None,
    cap_s: float | None = None,
) -> int | None:
    """Resolve an upstream rate-limit signal to integer seconds-to-wait.

    Returns ``None`` when no recognised signal is present (the caller then falls
    back to its own backoff). A recognised-but-past signal resolves to ``0`` (wait
    nothing) rather than ``None`` so the caller doesn't mistake "reset already
    elapsed" for "no signal". ``now_epoch``/``cap_s`` are injectable for testing.
    """
    now = now_epoch if now_epoch is not None else datetime.now(UTC).timestamp()

    seconds = _from_retry_after(_get(headers, "retry-after"), now=now)
    if seconds is None:
        seconds = _from_reset_epoch(_get(headers, "x-ratelimit-reset"), now=now)
    if seconds is None:
        return None

    seconds = max(0, seconds)
    if cap_s is not None:
        seconds = min(seconds, max(0, int(cap_s)))
    return seconds


def _get(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup that tolerates a plain dict or httpx.Headers."""
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = headers.get(name)  # httpx.Headers is already case-insensitive
        if value is not None:
            return value
    lowered = name.lower()
    for key, val in headers.items():
        if key.lower() == lowered:
            return val
    return None


def _from_retry_after(value: str | None, *, now: float) -> int | None:
    """Parse ``Retry-After`` as integer seconds, else as an HTTP-date delta."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    # Not a bare integer → try the RFC 7231 HTTP-date form.
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return math.ceil(when.timestamp() - now)


def _from_reset_epoch(value: str | None, *, now: float) -> int | None:
    """Parse ``X-RateLimit-Reset`` as a unix-epoch reset time → seconds from now."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        reset = float(raw)
    except ValueError:
        return None
    return math.ceil(reset - now)
