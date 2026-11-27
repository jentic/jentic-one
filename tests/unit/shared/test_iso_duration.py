"""ISO 8601 durations for a binding's idle limit on the wire."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from jentic_one.shared.iso_duration import (
    IdlePeriodRequest,
    IdlePeriodResponse,
    format_iso_duration,
    parse_iso_duration,
)

DAY = 86_400


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("P14D", 14 * DAY),
        ("P2W", 14 * DAY),
        ("PT36H", 36 * 3_600),
        ("P1DT12H", DAY + 12 * 3_600),
        ("PT90M", 5_400),
        ("PT45S", 45),
        ("p7d", 7 * DAY),
    ],
)
def test_parses_fixed_length_durations(text: str, seconds: int) -> None:
    assert parse_iso_duration(text) == seconds


@pytest.mark.parametrize("text", ["P", "PT", "P1M", "P1Y", "P1.5D", "14d", "1209600", "P1DT"])
def test_refuses_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match="ISO 8601"):
        parse_iso_duration(text)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(14 * DAY, "P14D"), (DAY + 12 * 3_600, "P1DT12H"), (5_400, "PT1H30M"), (0, "PT0S")],
)
def test_formats_the_shortest_duration(seconds: int, text: str) -> None:
    assert format_iso_duration(seconds) == text
    assert seconds == 0 or parse_iso_duration(text) == seconds


class _Request(BaseModel):
    max_idle_period: IdlePeriodRequest | None = None


class _Response(BaseModel):
    max_idle_period: IdlePeriodResponse | None = None


def test_a_request_takes_only_a_duration_string_within_range() -> None:
    assert _Request.model_validate({"max_idle_period": "P2W"}).max_idle_period == 14 * DAY
    assert _Request.model_validate({"max_idle_period": None}).max_idle_period is None
    for bad in (14 * DAY, "PT23H", "P1000D"):
        with pytest.raises(ValidationError):
            _Request.model_validate({"max_idle_period": bad})


def test_a_response_renders_seconds_as_a_duration() -> None:
    assert _Response(max_idle_period=14 * DAY).model_dump(mode="json") == {
        "max_idle_period": "P14D"
    }
    assert _Response().model_dump(mode="json") == {"max_idle_period": None}
