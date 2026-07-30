"""Unit tests for ExecutionService actor field propagation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from jentic_one.admin.services.execution_service import ExecutionService


def _make_record(**overrides: Any) -> MagicMock:
    defaults = {
        "id": "exec_001",
        "toolkit_id": "tk_test000000000000000000",
        "trace_id": "a" * 32,
        "started_at": datetime(2026, 6, 1, tzinfo=UTC),
        "duration_ms": 100,
        "status": "completed",
        "operation_id": "getThing",
        "api_vendor": "example",
        "api_name": "api",
        "api_version": "1.0.0",
        "api_host": None,
        "pinned_revisions": None,
        "http_status": 200,
        "error": None,
        "created_at": datetime(2026, 6, 1, tzinfo=UTC),
        "actor_id": "usr_default",
        "actor_type": "user",
        "origin": None,
        # #740: credential attribution — always populate the axis so
        # ``_to_view``'s ``getattr(record, "credential_*", None)`` reads a real
        # value instead of a MagicMock autospec attribute (which Pydantic then
        # rejects as a non-string).
        "credential_id": None,
        "credential_name": None,
    }
    defaults.update(overrides)
    record = MagicMock()
    for k, v in defaults.items():
        setattr(record, k, v)
    return record


def test_to_view_populates_actor_fields() -> None:
    record = _make_record(actor_id="agt_abc", actor_type="agent")
    view = ExecutionService._to_view(record)
    assert view.actor_id == "agt_abc"
    assert view.actor_type == "agent"


def test_to_view_actor_fields_from_defaults() -> None:
    record = _make_record()
    view = ExecutionService._to_view(record)
    assert view.actor_id == "usr_default"
    assert view.actor_type == "user"


def test_to_view_toolkit_name_populated_from_names_map() -> None:
    record = _make_record(toolkit_id="tk_abc123")
    names_map = {"tk_abc123": "My Toolkit"}
    view = ExecutionService._to_view(record, names_map=names_map)
    assert view.toolkit_name == "My Toolkit"


def test_to_view_toolkit_name_none_when_not_in_map() -> None:
    record = _make_record(toolkit_id="tk_missing")
    names_map = {"tk_other": "Other Toolkit"}
    view = ExecutionService._to_view(record, names_map=names_map)
    assert view.toolkit_name is None


def test_to_view_toolkit_name_none_when_no_map_provided() -> None:
    record = _make_record(toolkit_id="tk_abc123")
    view = ExecutionService._to_view(record)
    assert view.toolkit_name is None
