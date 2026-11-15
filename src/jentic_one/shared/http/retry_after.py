"""Parse an HTTP ``Retry-After`` value to whole seconds-to-wait.

The one parser the broker's retry runner and the MCP transport-error relay
share. ``Retry-After`` is either delay-seconds or an HTTP-date (RFC 9110
§10.2.3):

- Delay-seconds is a non-negative decimal. A fractional value (``1.5``) is
  accepted and rounded **up**, so a caller never retries early; a sign, an
  exponent or a non-ASCII digit is not a delay.
- An HTTP-date is resolved against "now" in UTC. ``-0000`` (and any other
  zone-less form ``email.utils`` reads as naive) is pinned to UTC, never read
  as server-local time. A date already past resolves to ``0``.
- Either form is capped at :data:`MAX_RETRY_AFTER_S` (one day), so an absurd
  value is never relayed as a wait.

Anything else is ``None``, so no made-up delay is relayed.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

_DELAY_SECONDS = re.compile(r"([0-9]+)(?:\.([0-9]+))?")
_TIME_OF_DAY = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}")
_NUMERIC_OFFSET = re.compile(r"([+-])([0-9]{2}):?([0-9]{2})")
_YEAR_4DIGIT = re.compile(r"[0-9]{4}")

#: The longest wait the parser reports; a larger delay or later date is capped.
MAX_RETRY_AFTER_S = 86_400
_MAX_DIGITS = len(str(MAX_RETRY_AFTER_S))


def _normalize_http_date(raw: str) -> str | None:
    """Validate and normalise an HTTP-date before passing to ``email.utils``.

    Accepts RFC 9110 §5.6.7 dates (IMF-fixdate, RFC 850, ANSI C ``asctime``)
    in ``GMT``/``UTC``, numeric ``±HHMM``/``±HH:MM`` offsets (``HH <= 23``,
    ``MM <= 59``), and zone-less RFC 1123 dates. Rejects other named zones
    (``EST``, ``PST``, ``Z``), fractional seconds, missing space after the
    weekday comma, and out-of-range numeric offsets.
    """
    if "," in raw:
        head, sep, rest = raw.partition(",")
        if not head or not sep or not rest.startswith(" ") or "," in rest:
            return None
    tokens = raw.split()
    if len(tokens) < 4:
        return None
    if _TIME_OF_DAY.fullmatch(tokens[-1]) is not None:
        return raw
    if _TIME_OF_DAY.fullmatch(tokens[-2]) is None:
        return None
    last = tokens[-1]
    if "," not in raw and len(tokens) == 5 and _YEAR_4DIGIT.fullmatch(last) is not None:
        return raw
    if last.upper() in {"GMT", "UTC"}:
        tokens[-1] = last.upper()
        return " ".join(tokens)
    offset = _NUMERIC_OFFSET.fullmatch(last)
    if offset is None:
        return None
    hh, mm = int(offset.group(2)), int(offset.group(3))
    if hh > 23 or mm > 59:
        return None
    tokens[-1] = f"{offset.group(1)}{offset.group(2)}{offset.group(3)}"
    return " ".join(tokens)


def parse_retry_after(value: str | None, *, now_epoch: float | None = None) -> int | None:
    """Whole seconds a ``Retry-After`` value asks the caller to wait.

    ``now_epoch`` (unix seconds) is injectable for testing; it defaults to the
    current UTC time.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    delay = _DELAY_SECONDS.fullmatch(raw)
    if delay is not None:
        whole, fraction = delay.group(1).lstrip("0"), delay.group(2) or ""
        if len(whole) > _MAX_DIGITS:
            return MAX_RETRY_AFTER_S
        seconds = int(whole or "0") + (1 if fraction.strip("0") else 0)
        return min(seconds, MAX_RETRY_AFTER_S)
    normalized = _normalize_http_date(raw)
    if normalized is None:
        return None
    try:
        when = parsedate_to_datetime(normalized)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = now_epoch if now_epoch is not None else datetime.now(UTC).timestamp()
    return min(max(0, math.ceil(when.timestamp() - now)), MAX_RETRY_AFTER_S)
