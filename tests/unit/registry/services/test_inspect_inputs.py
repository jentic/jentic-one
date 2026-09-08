"""Unit tests for the inspect inputs projector (issue #768)."""

from __future__ import annotations

from typing import Any

from jentic_one.registry.services.inspect.inputs import build_operation_inputs


def test_groups_parameters_by_location() -> None:
    raw: dict[str, Any] = {
        "parameters": [
            {"name": "page_id", "in": "path", "required": True},
            {"name": "page_size", "in": "query", "schema": {"type": "integer"}},
            {"name": "Notion-Version", "in": "header", "required": True},
        ]
    }
    inputs = build_operation_inputs(raw)
    assert [p.name for p in inputs.path] == ["page_id"]
    assert [p.name for p in inputs.query] == ["page_size"]
    assert [p.name for p in inputs.header] == ["Notion-Version"]
    assert inputs.query[0].schema_ == {"type": "integer"}
    assert inputs.path[0].required is True


def test_extracts_json_request_body_schema() -> None:
    raw: dict[str, Any] = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"type": "object", "properties": {"parent": {}}}}
            },
        }
    }
    inputs = build_operation_inputs(raw)
    assert inputs.body is not None
    assert inputs.body.required is True
    assert inputs.body.content_type == "application/json"
    assert inputs.body.schema_ == {"type": "object", "properties": {"parent": {}}}


def test_prefers_json_media_type_over_others() -> None:
    raw: dict[str, Any] = {
        "requestBody": {
            "content": {
                "text/plain": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "object"}},
            }
        }
    }
    inputs = build_operation_inputs(raw)
    assert inputs.body is not None
    assert inputs.body.content_type == "application/json"
    assert inputs.body.schema_ == {"type": "object"}


def test_falls_back_to_first_media_type_when_no_json() -> None:
    raw: dict[str, Any] = {
        "requestBody": {
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    }
    inputs = build_operation_inputs(raw)
    assert inputs.body is not None
    assert inputs.body.content_type == "text/csv"


def test_body_without_content_still_surfaces_required() -> None:
    raw: dict[str, Any] = {"requestBody": {"required": True}}
    inputs = build_operation_inputs(raw)
    assert inputs.body is not None
    assert inputs.body.required is True
    assert inputs.body.schema_ is None


def test_empty_or_missing_raw_operation_yields_empty_inputs() -> None:
    values: list[Any] = [None, {}, [], "not-a-dict"]
    for value in values:
        inputs = build_operation_inputs(value)
        assert inputs.path == []
        assert inputs.query == []
        assert inputs.header == []
        assert inputs.body is None


def test_skips_malformed_and_unresolvable_parameters() -> None:
    raw: dict[str, Any] = {
        "parameters": [
            {"name": "ok", "in": "query"},
            {"in": "query"},  # missing name
            {"name": "no_location"},  # missing in
            {"$ref": "#/components/parameters/Shared"},  # unresolvable here
            {"name": "session", "in": "cookie"},  # location we don't group
            "not-a-dict",
        ]
    }
    inputs = build_operation_inputs(raw)
    assert [p.name for p in inputs.query] == ["ok"]
    assert inputs.path == []
    assert inputs.header == []
