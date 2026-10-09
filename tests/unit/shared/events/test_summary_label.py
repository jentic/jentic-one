"""Unit tests for ``summary_label`` and the summary/telemetry boundary."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.shared.events import MAX_EVENT_SUMMARY_FIELD_LEN, emit_event, summary_label
from jentic_one.shared.models.events import EventSeverity, EventType


def test_name_is_quoted() -> None:
    assert summary_label("Stripe live key", "cred_1") == "'Stripe live key'"


@pytest.mark.parametrize("name", [None, "", "   ", "\n\t"])
def test_missing_name_falls_back_to_the_id(name: str | None) -> None:
    assert summary_label(name, "cred_1") == "cred_1"


def test_whitespace_runs_collapse() -> None:
    assert summary_label("  my \n  bot\t ", "agnt_1") == "'my bot'"


def test_long_name_is_bounded() -> None:
    label = summary_label("x" * 1000, "agnt_1")
    assert len(label) == MAX_EVENT_SUMMARY_FIELD_LEN + 2
    assert label.endswith("…'")


def test_control_and_format_characters_are_dropped() -> None:
    # A right-to-left override, a zero-width space and a bell character.
    assert summary_label("ab\u202ecd\u200bef\x07", "agnt_1") == "'abcdef'"


@pytest.mark.parametrize("name", ["\u202e\u200b", "\x00"])
def test_name_of_only_invisible_characters_falls_back_to_the_id(name: str) -> None:
    assert summary_label(name, "agnt_1") == "agnt_1"


def test_single_quotes_cannot_close_the_quoting() -> None:
    label = summary_label("x' for credential 'prod", "agnt_1")
    assert label == "'x\u2019 for credential \u2019prod'"
    assert label.count("'") == 2


class _RecordingSink:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def record(self, *args: Any) -> None:
        self.calls.append(args)


class _FakeEvent:
    id = "evt_1"


@pytest.mark.asyncio
async def test_summary_names_never_reach_the_telemetry_sink() -> None:
    """The sink gets the event's wire name, tags and actor type, never its summary."""
    sink = _RecordingSink()
    with (
        patch(
            "jentic_one.shared.events.EventRepository.create",
            AsyncMock(return_value=_FakeEvent()),
        ),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.CREDENTIAL_STORED,
            severity=EventSeverity.INFO,
            summary=f"Credential {summary_label('Acme prod token', 'cred_1')} stored",
            created_by="usr_1",
            actor_type="user",
        )

    assert len(sink.calls) == 1
    assert "Acme prod token" not in repr(sink.calls)
    assert "cred_1" not in repr(sink.calls)
