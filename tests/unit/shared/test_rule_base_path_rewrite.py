"""Unit tests for the base-path rule rewrite decision (#1424)."""

from __future__ import annotations

import pytest

from jentic_one.registry.services.api_path_shapes import server_base_path_pattern
from jentic_one.shared.permissions.base_path_rewrite import (
    ApiPathShape,
    RewriteOutcome,
    SkipReason,
    decide_rewrite,
    path_applies_to_template,
)

_REGION = {"region": {"enum": ["eu", "us"], "default": "eu"}}


def _shape(server_url: str, variables: object, *templates: str) -> ApiPathShape:
    pattern = server_base_path_pattern(server_url, variables)
    return ApiPathShape(
        base_path_patterns=(pattern,) if pattern else (),
        operation_templates=templates,
    )


_PETSTORE = _shape("https://petstore.example.com/api/v3", None, "/pet", "/pet/{petId}")
_WIDGETS = _shape("http://{host}:18765/{region}", _REGION, "/widgets", "/widgets/{id}")
_FREEFORM = _shape("https://h.example.com/{tenant}", {"tenant": {}}, "/widgets/{id}", "/foo")


@pytest.mark.parametrize(
    ("server_url", "variables", "expected"),
    [
        ("https://api.example.com", None, None),
        ("https://api.example.com/", None, None),
        ("https://api.example.com/api/v3/", None, r"/api/v3"),
        ("http://{host}:18765/{region}", _REGION, r"/(?:eu|us)"),
        ("https://h.example.com/{tenant}/v1", {"tenant": {}}, r"/[^/]+/v1"),
        ("/api/v3", None, r"/api/v3"),
    ],
)
def test_server_base_path_pattern(server_url: str, variables: object, expected: str | None) -> None:
    assert server_base_path_pattern(server_url, variables) == expected


@pytest.mark.parametrize(
    ("path", "mode", "template", "applies"),
    [
        ("/pet", "prefix", "/pet/{petId}", True),
        ("/pe", "prefix", "/pet/{petId}", True),
        ("/pet/9/x", "prefix", "/pet/{petId}", False),
        ("/pet/9", "exact", "/pet/{petId}", True),
        ("/pet", "exact", "/pet/{petId}", False),
        ("/api/v3/pet", "prefix", "/pet/{petId}", False),
    ],
)
def test_path_applies_to_template(path: str, mode: str, template: str, applies: bool) -> None:
    assert path_applies_to_template(path, mode, template) is applies


@pytest.mark.parametrize(
    ("path", "mode", "shape", "new_path"),
    [
        ("/api/v3/pet", "prefix", _PETSTORE, "/pet"),
        ("/api/v3/pet/9", "exact", _PETSTORE, "/pet/9"),
        ("/api/v3", "prefix", _PETSTORE, "/"),
        ("/eu/widgets", "prefix", _WIDGETS, "/widgets"),
        ("/us/widgets/7", "exact", _WIDGETS, "/widgets/7"),
    ],
)
def test_rewrites_base_path_rules(path: str, mode: str, shape: ApiPathShape, new_path: str) -> None:
    decision = decide_rewrite(path, mode, [shape])
    assert decision.outcome is RewriteOutcome.REWRITE
    assert decision.new_path == new_path


@pytest.mark.parametrize(
    ("path", "mode", "shape"),
    [
        # Already spec-relative.
        ("/pet", "prefix", _PETSTORE),
        ("/widgets", "prefix", _WIDGETS),
        # A free-form base must not eat a real first segment: ``/widgets/foo``
        # applies to ``/widgets/{id}`` as-is, so it is not rewritten to ``/foo``.
        ("/widgets/foo", "exact", _FREEFORM),
        # Not under any base.
        ("/other", "prefix", _WIDGETS),
        # ``/eux`` is not the ``eu`` segment.
        ("/eux/widgets", "prefix", _WIDGETS),
    ],
)
def test_leaves_relative_or_unrelated_rules_alone(
    path: str, mode: str, shape: ApiPathShape
) -> None:
    assert decide_rewrite(path, mode, [shape]).outcome is RewriteOutcome.UNCHANGED


def test_regex_rules_are_reported_not_rewritten() -> None:
    decision = decide_rewrite("/api/v3/pet/.*", "regex", [_PETSTORE])
    assert decision.outcome is RewriteOutcome.SKIPPED
    assert decision.reason is SkipReason.REGEX_NOT_REWRITABLE


def test_stripped_path_matching_no_operation_is_reported() -> None:
    decision = decide_rewrite("/api/v3/nope", "prefix", [_PETSTORE])
    assert decision.reason is SkipReason.MATCHES_NO_OPERATION


def test_ambiguous_bases_are_reported() -> None:
    shape = ApiPathShape(
        base_path_patterns=(r"/api", r"/api/v3"),
        operation_templates=("/v3/pet", "/pet"),
    )
    decision = decide_rewrite("/api/v3/pet", "prefix", [shape])
    assert decision.reason is SkipReason.AMBIGUOUS_BASE


def test_rule_set_spanning_disagreeing_apis_is_reported() -> None:
    decision = decide_rewrite("/api/v3/pet", "prefix", [_PETSTORE, _WIDGETS])
    assert decision.reason is SkipReason.RULE_SET_MIXED_APIS


def test_missing_api_is_reported() -> None:
    decision = decide_rewrite("/api/v3/pet", "prefix", [None])
    assert decision.reason is SkipReason.API_NOT_FOUND


def test_rewrite_is_idempotent() -> None:
    first = decide_rewrite("/eu/widgets", "prefix", [_WIDGETS])
    assert first.new_path is not None
    assert decide_rewrite(first.new_path, "prefix", [_WIDGETS]).outcome is RewriteOutcome.UNCHANGED
