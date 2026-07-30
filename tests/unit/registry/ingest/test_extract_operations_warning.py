"""Unit tests for the security_schemes_unused import diagnostic (#772)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from jentic_one.registry.ingest.stages.extract_operations import ExtractOperationsStage

REVISION_ID = uuid.uuid4()


def _warn(content: dict[str, Any], raw_ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with structlog.testing.capture_logs() as logs:
        ExtractOperationsStage._warn_if_security_unresolved(REVISION_ID, content, raw_ops)
    return [log for log in logs if log["event"] == "security_schemes_unused"]


def test_warns_when_schemes_declared_but_nothing_resolves() -> None:
    content = {"components": {"securitySchemes": {"apiKey": {"type": "apiKey"}}}}
    warnings = _warn(content, [{"path": "/a", "method": "GET"}])
    assert len(warnings) == 1
    assert warnings[0]["scheme_names"] == ["apiKey"]
    assert warnings[0]["operation_count"] == 1


def test_no_warning_when_an_operation_resolves_security() -> None:
    content = {"components": {"securitySchemes": {"apiKey": {"type": "apiKey"}}}}
    raw_ops = [{"path": "/a", "method": "GET", "security": [{"apiKey": []}]}]
    assert _warn(content, raw_ops) == []


def test_no_warning_when_no_schemes_declared() -> None:
    assert _warn({}, [{"path": "/a", "method": "GET"}]) == []


def test_no_warning_when_no_operations() -> None:
    content = {"components": {"securitySchemes": {"apiKey": {"type": "apiKey"}}}}
    assert _warn(content, []) == []


def test_non_string_scheme_keys_do_not_crash() -> None:
    # A hostile/malformed spec can carry non-string keys under securitySchemes
    # (YAML allows int/bool keys). Coercing to str before sorting must not
    # raise — the import should still complete and warn.
    content = {"components": {"securitySchemes": {1: {"type": "apiKey"}, "b": {}}}}
    warnings = _warn(content, [{"path": "/a", "method": "GET"}])
    assert len(warnings) == 1
    assert warnings[0]["scheme_names"] == ["1", "b"]


def test_non_dict_components_or_schemes_do_not_crash() -> None:
    assert _warn({"components": "nope"}, [{"path": "/a", "method": "GET"}]) == []
    assert _warn({"components": {"securitySchemes": "nope"}}, [{"path": "/a"}]) == []
