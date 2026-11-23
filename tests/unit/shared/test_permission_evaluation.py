"""``first_matching_rule`` fails closed for deny / require-approval rules it cannot match."""

from __future__ import annotations

from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    first_matching_rule,
    is_allowed,
)
from jentic_one.shared.permissions.matching import compile_matcher

_UNSAFE = "/repos/(.+)+"


def _rule(effect: str, path: str, mode: str, methods: frozenset[str] | None = None):
    return PermissionRule(
        effect=effect,
        methods=methods,
        path=compile_matcher(path, mode),
        operations=None,
    )


def _eval(rules: list[PermissionRule], method: str = "DELETE", path: str = "/repos/acme/prod"):
    return first_matching_rule(rules, method=method, path=path, operation_id=None)


def test_never_deny_rule_halts_as_deny() -> None:
    rules = [_rule("deny", _UNSAFE, "regex"), _rule("allow", "/repos/", "prefix")]
    assert rules[0].path is not None and rules[0].path.never
    idx = _eval(rules)
    assert idx == 0
    assert is_allowed(rules, idx) is False


def test_never_require_approval_halts_as_default_deny() -> None:
    rules = [_rule("require-approval", _UNSAFE, "regex"), _rule("allow", "/repos/", "prefix")]
    assert _eval(rules) is None


def test_never_allow_rule_still_skipped() -> None:
    rules = [_rule("allow", _UNSAFE, "regex"), _rule("allow", "/repos/", "prefix")]
    assert _eval(rules) == 1


def test_never_deny_rule_respects_its_method_scope() -> None:
    rules = [
        _rule("deny", _UNSAFE, "regex", frozenset({"DELETE"})),
        _rule("allow", "/repos/", "prefix"),
    ]
    assert _eval(rules, method="GET") == 1
    assert _eval(rules, method="DELETE") == 0


def test_overlong_path_halts_deny_rule() -> None:
    rules = [_rule("deny", "/repos/.*", "regex"), _rule("allow", "/repos/", "prefix")]
    long_path = "/repos/" + "a" * 5000
    idx = _eval(rules, path=long_path)
    assert idx == 0
    assert is_allowed(rules, idx) is False


def test_overlong_path_does_not_match_allow_rule() -> None:
    rules = [_rule("allow", "/repos/.*", "regex")]
    assert _eval(rules, path="/repos/" + "a" * 5000) is None
