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

#: The longest wait the parser reports; a larger delay or later date is capped.
MAX_RETRY_AFTER_S = 86_400
_MAX_DIGITS = len(str(MAX_RETRY_AFTER_S))


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
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = now_epoch if now_epoch is not None else datetime.now(UTC).timestamp()
    return min(max(0, math.ceil(when.timestamp() - now)), MAX_RETRY_AFTER_S)
