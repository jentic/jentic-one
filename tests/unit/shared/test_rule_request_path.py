"""Unit tests for the spec-relative rule path (#1424).

Binding permission rules are authored against an API's spec paths (relative to
its server URL), so enforcement must evaluate them on the same basis — never
the full upstream path, which carries the server base (``/v3``, ``/{region}``).
"""

from __future__ import annotations

import re

import pytest

from jentic_one.registry.core.url_index import (
    build_path_regex,
    build_server_index_entries,
    expand_path_template,
    normalize_path,
    resolve_server_variable_groups,
)
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    evaluate_rules,
    first_matching_rule,
    rule_request_path,
)
from jentic_one.shared.permissions.matching import compile_matcher

# ---------------------------------------------------------------------------
# expand_path_template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "params", "expected"),
    [
        ("/widgets", {}, "/widgets"),
        ("/widgets/{id}", {"id": "42"}, "/widgets/42"),
        ("/repos/{owner}/{repo}", {"owner": "octo", "repo": "hello"}, "/repos/octo/hello"),
        ("/files/{+path}", {"path": "a/b/c.txt"}, "/files/a/b/c.txt"),
        ("/users/{user-id}", {"user_id": "u1"}, "/users/u1"),
        ("/widgets/", {}, "/widgets"),
        ("/", {}, "/"),
    ],
)
def test_expand_path_template(template: str, params: dict[str, str], expected: str) -> None:
    assert expand_path_template(template, params) == expected


def test_expand_path_template_missing_param_is_none() -> None:
    assert expand_path_template("/widgets/{id}", {}) is None


def test_expand_path_template_round_trips_the_index_regex() -> None:
    """Substituting a row's captured groups rebuilds the path the row matched."""
    regex = build_path_regex("/repos/{owner}/{repo}/issues/{number}")
    match = regex.fullmatch("/repos/octo/hello/issues/7")
    assert match is not None
    rebuilt = expand_path_template("/repos/{owner}/{repo}/issues/{number}", match.groupdict())
    assert rebuilt == "/repos/octo/hello/issues/7"


@pytest.mark.parametrize(
    ("server_url", "variables", "request_path", "expected"),
    [
        ("https://petstore.example.com/api/v3", None, "/api/v3/pet/9", "/pet/9"),
        (
            "http://{host}:18765/{region}",
            {"host": {"default": "localhost"}, "region": {"enum": ["eu", "us"], "default": "eu"}},
            "/eu/widgets/9",
            "/widgets/9",
        ),
        (
            "https://api.example.com/{version}",
            {"version": {"default": "v1"}},
            "/v7/widgets/9",
            "/widgets/9",
        ),
    ],
)
def test_relative_path_strips_the_server_base(
    server_url: str, variables: object, request_path: str, expected: str
) -> None:
    """The rebuilt path excludes the server base for static and templated bases."""
    template = "/widgets/{id}" if "widgets" in request_path else "/pet/{id}"
    expansion = build_server_index_entries(server_url, variables, template)
    path = normalize_path(request_path)
    for entry in expansion.entries:
        match = re.fullmatch(entry.path_regex, path)
        if match is None:
            continue
        resolved = resolve_server_variable_groups(match.groupdict())
        assert resolved is not None
        assert expand_path_template(template, resolved.path_params) == expected
        return
    pytest.fail(f"no index entry matched {request_path}")


# ---------------------------------------------------------------------------
# rule_request_path
# ---------------------------------------------------------------------------


def test_rule_request_path_prefers_relative_path() -> None:
    assert rule_request_path("/widgets", "https://h.example.com/eu/widgets") == "/widgets"


def test_rule_request_path_falls_back_to_normalized_upstream_path() -> None:
    assert rule_request_path(None, "https://h.example.com/eu/%61dmin/./x/") == "/eu/admin/x"


# ---------------------------------------------------------------------------
# Shared evaluation loop
# ---------------------------------------------------------------------------


def _rule(effect: str, path: str | None, mode: str = "prefix") -> PermissionRule:
    return PermissionRule(
        effect=effect,
        methods=frozenset({"GET"}),
        path=compile_matcher(path, mode),
        operations=None,
    )


def test_base_path_api_rule_on_spec_path_allows() -> None:
    """Pins #1424: ``allow GET prefix /widgets`` allows a call that reaches
    upstream as ``/eu/widgets`` once evaluated on the relative path."""
    rules = [_rule("allow", "/widgets")]
    path = rule_request_path("/widgets", "http://localhost:18765/eu/widgets")
    assert evaluate_rules(rules, method="GET", path=path, operation_id=None) is True


def test_encoded_path_cannot_slip_past_a_deny() -> None:
    """A percent-encoded spelling of a denied path is still denied: rules see
    the normalized path discovery resolved, not the raw request bytes."""
    rules = [_rule("deny", "/admin"), _rule("allow", "/")]
    path = rule_request_path(None, "https://h.example.com/%61dmin/users")
    assert evaluate_rules(rules, method="GET", path=path, operation_id=None) is False


def test_first_matching_rule_reports_the_deciding_index() -> None:
    rules = [_rule("deny", "/widgets/secret", "exact"), _rule("allow", "/widgets")]
    assert first_matching_rule(rules, method="GET", path="/widgets/1", operation_id=None) == 1
    assert first_matching_rule(rules, method="GET", path="/widgets/secret", operation_id=None) == 0
    assert first_matching_rule(rules, method="GET", path="/other", operation_id=None) is None


def test_first_matching_rule_skips_condition_less_allow() -> None:
    rules = [PermissionRule(effect="allow", methods=None, path=None, operations=None)]
    assert first_matching_rule(rules, method="GET", path="/x", operation_id=None) is None
