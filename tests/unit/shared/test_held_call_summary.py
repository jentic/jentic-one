"""Unit tests for the summary of a held call's lifecycle events."""

from __future__ import annotations

from jentic_one.shared.events import MAX_EVENT_SUMMARY_FIELD_LEN
from jentic_one.shared.jobs.hold import held_call_summary


def test_names_the_call_the_agent_and_the_outcome() -> None:
    summary = held_call_summary(
        agent_name="billing-bot", agent_id="agnt_1", method="POST", path="/orders", outcome="denied"
    )
    assert summary == "Call POST /orders by agent 'billing-bot' denied"


def test_an_agent_without_a_name_falls_back_to_its_id() -> None:
    summary = held_call_summary(
        agent_name=None, agent_id="agnt_1", method="GET", path="/items", outcome="expired"
    )
    assert summary == "Call GET /items by agent agnt_1 expired"


def test_a_long_path_is_bounded() -> None:
    path = "/" + "x" * 600
    summary = held_call_summary(
        agent_name="a", agent_id="agnt_1", method="GET", path=path, outcome="approved"
    )
    assert path not in summary
    assert "/" + "x" * (MAX_EVENT_SUMMARY_FIELD_LEN - 2) + "…" in summary
    assert len(summary) <= 512
    assert summary.endswith("by agent 'a' approved")
