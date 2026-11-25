"""Durations an agent or operator types for how long access should last.

``"30m"``, ``"8h"``, ``"7d"`` (or a bare number of seconds) — the same fixed
units the console's duration control offers — or the ISO 8601 duration the
API itself uses (``"P7D"``, ``"PT8H"``). There are deliberately no
months or years: their length depends on the calendar.
"""

from __future__ import annotations

import re

from jentic_one.shared.iso_duration import parse_iso_duration

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
_DURATION = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$")


def parse_duration_seconds(text: str) -> int:
    """Seconds in ``text`` (``"7d"``, ``"8h"``, ``"90m"``, ``"45s"``, ``"3600"`` or ``"P7D"``).

    Raises :class:`ValueError` for anything else, or for zero.
    """
    if text.strip().upper().startswith("P"):
        seconds = parse_iso_duration(text)
        if seconds <= 0:
            raise ValueError("a duration must be longer than zero")
        return seconds
    match = _DURATION.match(text.lower())
    if match is None:
        raise ValueError(
            f"invalid duration {text!r}: use a whole number with s, m, h or d, for example 7d"
        )
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2) or "s"]
    if seconds <= 0:
        raise ValueError("a duration must be longer than zero")
    return seconds
