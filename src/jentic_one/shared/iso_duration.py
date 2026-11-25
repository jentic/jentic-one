"""ISO 8601 durations: how the API and config carry every length of time.

A duration is an ISO 8601 string (``"P30D"``, ``"PT36H"``, ``"P2W"``,
``"P1DT12H"``); storage and every layer below the schemas and config keep
whole seconds. Only fixed-length units are accepted — weeks, days, hours,
minutes and seconds — never years or months, whose length depends on the
calendar, and never fractions.

- :func:`iso_duration` builds a request or config field: it accepts only a
  duration string (a bare number is refused), checks a range, and holds
  seconds once validated.
- :data:`IsoDurationResponse` is a response field: built from seconds (or its
  own rendered string, so a response can be rebuilt from another's dump), it
  renders in JSON as the shortest duration string (``2_592_000`` → ``"P30D"``).

Dependency-free, so config can use it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import AfterValidator, BeforeValidator, PlainSerializer, WithJsonSchema

DAY = 86_400
#: The longest duration anywhere (999 days, the console's largest custom amount).
MAX_DURATION_SECONDS = 999 * DAY

_DURATION = re.compile(
    r"^P(?:(?P<w>\d+)W)?(?:(?P<d>\d+)D)?"
    r"(?:T(?=\d)(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)
_UNIT_SECONDS = {"w": 7 * DAY, "d": DAY, "h": 3_600, "m": 60, "s": 1}


def parse_iso_duration(text: str) -> int:
    """Whole seconds in an ISO 8601 duration (``"P14D"``, ``"PT36H"``, ``"P1DT12H"``).

    Raises :class:`ValueError` for anything else: an empty duration (``"P"``),
    fractions, or years and months (``"P1M"``, ``"P1Y"``).
    """
    match = _DURATION.match(text.strip().upper())
    if match is None or not any(match.groupdict().values()):
        raise ValueError(
            f"invalid ISO 8601 duration {text!r}: use weeks, days, hours, minutes or seconds,"
            " for example P30D or PT36H (no years or months)"
        )
    return sum(int(v) * _UNIT_SECONDS[k] for k, v in match.groupdict().items() if v)


def format_iso_duration(seconds: int) -> str:
    """The shortest ISO 8601 duration for ``seconds`` (``1_209_600`` → ``"P14D"``)."""
    days, rest = divmod(int(seconds), DAY)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    time = "".join(f"{n}{u}" for n, u in ((hours, "H"), (minutes, "M"), (secs, "S")) if n)
    if not days and not time:
        return "PT0S"
    return f"P{f'{days}D' if days else ''}{f'T{time}' if time else ''}"


def _parse_strict(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("must be an ISO 8601 duration string, for example P30D")
    return parse_iso_duration(value)


def _parse_lenient(value: Any) -> Any:
    return parse_iso_duration(value) if isinstance(value, str) else value


_JSON_SCHEMA = {"type": "string", "format": "duration"}
#: Config's schema says the same with a pattern: generators map ``format:
#: duration`` to a native duration type that cannot read ISO 8601 (Go's
#: ``time.Duration`` in the installer's config struct).
_CONFIG_JSON_SCHEMA = {
    "type": "string",
    "pattern": r"^P(\d+W)?(\d+D)?(T(\d+H)?(\d+M)?(\d+S)?)?$",
}


def iso_duration(
    min_seconds: int, max_seconds: int = MAX_DURATION_SECONDS, *, config: bool = False
) -> Any:
    """A request or config field: an ISO 8601 string between the bounds, held as seconds."""

    def _check(seconds: int) -> int:
        if not min_seconds <= seconds <= max_seconds:
            raise ValueError(
                f"must be between {format_iso_duration(min_seconds)} and "
                f"{format_iso_duration(max_seconds)}"
            )
        return seconds

    return Annotated[
        int,
        BeforeValidator(_parse_strict),
        AfterValidator(_check),
        # Config dumps as it is written (a dump reloads); a request keeps seconds in
        # Python so services read them, and renders ISO 8601 in JSON.
        PlainSerializer(
            format_iso_duration, return_type=str, when_used="always" if config else "json"
        ),
        WithJsonSchema(_CONFIG_JSON_SCHEMA if config else _JSON_SCHEMA),
    ]


#: A response field: seconds in, an ISO 8601 duration string out (in JSON).
IsoDurationResponse = Annotated[
    int,
    BeforeValidator(_parse_lenient),
    PlainSerializer(format_iso_duration, return_type=str, when_used="json"),
    WithJsonSchema(_JSON_SCHEMA),
]

if TYPE_CHECKING:  # seconds, once validated
    IdlePeriodRequest = int
    LifetimeRequest = int
else:
    #: A request's idle limit: 1 day to 999 days.
    IdlePeriodRequest = iso_duration(DAY)
    #: A request's lifetime (how long access lasts): 1 second to 999 days.
    LifetimeRequest = iso_duration(1)

#: A response's idle limit.
IdlePeriodResponse = IsoDurationResponse
