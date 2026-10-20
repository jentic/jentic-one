"""Git repository/ref rules: selector validation, coverage by level, and rule evaluation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema
from jentic_one.shared.permissions.evaluation import (
    compile_rule,
    first_matching_rule,
    rule_verdict,
)
from jentic_one.shared.permissions.git_rules import (
    GitAction,
    GitItem,
    normalize_repo_name,
    parse_selector,
    selector_error,
)
from jentic_one.shared.permissions.matching import compile_matcher, validate_path

_FETCH = GitItem(GitAction.FETCH, "octo-org", "app")
_PUSH_MAIN = GitItem(GitAction.PUSH, "octo-org", "app", "refs/heads/main")
_PUSH_AGENT = GitItem(GitAction.PUSH, "octo-org", "app", "refs/heads/agent/feat-1")
_FORCE_AGENT = GitItem(GitAction.FORCE, "octo-org", "app", "refs/heads/agent/feat-1")
_PUSH_ADVERT = GitItem(GitAction.PUSH, "octo-org", "app")


@pytest.mark.parametrize(
    "selector",
    [
        "fetch:octo-org/app",
        "fetch:*/*",
        "push:octo-org/app",
        "push:Octo-Org/App.git:refs/heads/agent/*",
        "force:octo-org/app:refs/heads/scratch/*",
        "push:octo-org/a?p:refs/tags/v*",
    ],
)
def test_valid_selectors(selector: str) -> None:
    assert selector_error(selector) is None
    assert validate_path(selector, "git") is None
    assert parse_selector(selector) is not None


@pytest.mark.parametrize(
    ("selector", "fragment"),
    [
        (None, "needs a selector"),
        ("", "needs a selector"),
        ("pull:octo-org/app", "unknown git action"),
        ("push:octo-org", "'<owner>/<repo>'"),
        ("push:octo-org/app/x", "'<owner>/<repo>'"),
        ("push:octo org/app", "letters, digits"),
        ("fetch:octo-org/app:refs/heads/main", "takes no ref"),
        ("push:octo-org/app:refs/notes/*", "refs/heads/ or refs/tags/"),
        ("push:octo-org/app:refs/jentic/*", "refs/heads/ or refs/tags/"),
        ("push:octo-org/app:refs/heads/../x", "refs/heads/ or refs/tags/"),
        ("push:octo-org/app:refs/heads/a b", "refs/heads/ or refs/tags/"),
        ("push:octo-org/app:" + "x" * 1000, "maximum length"),
    ],
)
def test_invalid_selectors(selector: str | None, fragment: str) -> None:
    reason = selector_error(selector)
    assert reason is not None and fragment in reason
    error = validate_path(selector, "git")
    assert error is not None and error.code in ("invalid_git_selector",)


def test_a_git_selector_never_matches_a_path() -> None:
    matcher = compile_matcher("push:octo-org/app", "git")
    assert matcher is not None and matcher.never
    assert not matcher.matches("/octo-org/app.git/git-receive-pack")


def test_repo_names_are_normalized() -> None:
    assert normalize_repo_name("App.GIT") == "app"
    assert normalize_repo_name("app") == "app"
    selector = parse_selector("push:OCTO-org/APP.git")
    assert selector is not None
    assert selector.covers(GitItem(GitAction.PUSH, "octo-org", "app"), effect="allow")


def test_allow_covers_its_level_and_below() -> None:
    push = parse_selector("push:octo-org/app")
    assert push is not None
    assert push.covers(_FETCH, effect="allow")
    assert push.covers(_PUSH_MAIN, effect="allow")
    assert not push.covers(_FORCE_AGENT, effect="allow")
    fetch = parse_selector("fetch:octo-org/app")
    assert fetch is not None
    assert fetch.covers(_FETCH, effect="allow")
    assert not fetch.covers(_PUSH_ADVERT, effect="allow")


def test_deny_covers_its_level_and_above() -> None:
    push = parse_selector("push:octo-org/app")
    assert push is not None
    assert not push.covers(_FETCH, effect="deny")
    assert push.covers(_PUSH_MAIN, effect="deny")
    assert push.covers(_FORCE_AGENT, effect="deny")
    force = parse_selector("force:octo-org/app:refs/heads/main")
    assert force is not None
    assert not force.covers(_PUSH_MAIN, effect="deny")
    assert force.covers(
        GitItem(GitAction.FORCE, "octo-org", "app", "refs/heads/main"), effect="deny"
    )


def test_ref_globs() -> None:
    agent = parse_selector("push:octo-org/app:refs/heads/agent/*")
    assert agent is not None
    assert agent.covers(_PUSH_AGENT, effect="allow")
    assert not agent.covers(_PUSH_MAIN, effect="allow")
    # An allow rule's ref glob is ignored for an item that names no ref.
    assert agent.covers(_FETCH, effect="allow")
    assert agent.covers(_PUSH_ADVERT, effect="allow")
    # A deny rule's ref glob covers only an item that names a ref.
    assert not agent.covers(_PUSH_ADVERT, effect="deny")
    assert agent.covers(_PUSH_AGENT, effect="deny")
    single = parse_selector("push:octo-org/app:refs/tags/v?")
    assert single is not None
    assert single.covers(GitItem(GitAction.PUSH, "octo-org", "app", "refs/tags/v1"), effect="allow")
    assert not single.covers(
        GitItem(GitAction.PUSH, "octo-org", "app", "refs/tags/v1/x"), effect="allow"
    )


def test_repo_globs() -> None:
    selector = parse_selector("fetch:octo-*/*")
    assert selector is not None
    assert selector.covers(_FETCH, effect="allow")
    assert not selector.covers(GitItem(GitAction.FETCH, "other", "app"), effect="allow")


def test_require_approval_never_covers() -> None:
    selector = parse_selector("push:octo-org/app")
    assert selector is not None
    assert not selector.covers(_PUSH_MAIN, effect="require-approval")


def _rules(*rows: tuple[str, str | None, str]) -> list:  # type: ignore[type-arg]
    return [
        compile_rule(effect=effect, methods=None, path=path, match_mode=mode, operations=None)
        for effect, path, mode in rows
    ]


def test_path_rules_never_match_a_git_item() -> None:
    rules = _rules(("allow", ".*", "regex"), ("allow", "/", "prefix"))
    idx = first_matching_rule(
        rules,
        method="POST",
        path="/octo-org/app/git-receive-pack",
        operation_id=None,
        git_item=_PUSH_MAIN,
    )
    assert rule_verdict(rules, idx) == "deny"


def test_condition_less_deny_matches_a_git_item() -> None:
    rules = [
        compile_rule(effect="deny", methods=None, path=None, match_mode="regex", operations=None),
        *_rules(("allow", "push:octo-org/app", "git")),
    ]
    idx = first_matching_rule(
        rules, method="POST", path="/x", operation_id=None, git_item=_PUSH_MAIN
    )
    assert idx == 0


def test_git_rules_never_match_a_path_request() -> None:
    rules = _rules(("allow", "push:octo-org/app", "git"))
    idx = first_matching_rule(rules, method="GET", path="/octo-org/app", operation_id=None)
    assert idx is None


def test_first_match_wins_over_git_items() -> None:
    rules = _rules(
        ("deny", "push:octo-org/app:refs/heads/main", "git"),
        ("allow", "force:octo-org/app:refs/heads/agent/*", "git"),
        ("allow", "push:octo-org/app", "git"),
    )

    def verdict(item: GitItem) -> str:
        idx = first_matching_rule(rules, method="POST", path="/x", operation_id=None, git_item=item)
        return rule_verdict(rules, idx)

    assert verdict(_FETCH) == "allow"
    assert verdict(_PUSH_MAIN) == "deny"
    assert verdict(GitItem(GitAction.FORCE, "octo-org", "app", "refs/heads/main")) == "deny"
    assert verdict(_FORCE_AGENT) == "allow"
    assert verdict(GitItem(GitAction.FORCE, "octo-org", "app", "refs/heads/other")) == "deny"
    assert verdict(GitItem(GitAction.PUSH, "octo-org", "app", "refs/heads/other")) == "allow"
    assert verdict(GitItem(GitAction.FETCH, "octo-org", "other")) == "deny"


def test_an_invalid_stored_selector_never_matches() -> None:
    rules = _rules(("allow", "push:not a repo", "git"))
    assert rules[0].path is not None and rules[0].path.never
    idx = first_matching_rule(
        rules, method="POST", path="/x", operation_id=None, git_item=_PUSH_MAIN
    )
    assert idx is None


def test_a_held_git_rule_is_refused_at_save_time() -> None:
    with pytest.raises(ValidationError, match="'allow' or 'deny'"):
        PermissionRuleSchema(effect="require-approval", path="push:octo-org/app", match_mode="git")
    with pytest.raises(ValidationError, match="unknown git action"):
        PermissionRuleSchema(effect="allow", path="pull:octo-org/app", match_mode="git")
    rule = PermissionRuleSchema(effect="allow", path="push:octo-org/app", match_mode="git")
    assert rule.match_mode == "git"


@pytest.mark.parametrize(
    "extra",
    [{"methods": ["POST"]}, {"operations": ["op_1"]}, {"effect": "require-approval"}],
)
def test_git_rules_take_no_methods_operations_or_holds(extra: dict[str, object]) -> None:
    body: dict[str, object] = {
        "effect": "allow",
        "path": "push:octo-org/app",
        "match_mode": "git",
        **extra,
    }
    with pytest.raises(ValidationError):
        PermissionRuleSchema.model_validate(body)


def test_fetch_one_repo_push_only_to_one_namespace() -> None:
    rules = [
        compile_rule(
            effect="allow",
            methods=None,
            path="push:jentic/jentic-one-internal:refs/heads/jentic-e2e/*",
            match_mode="git",
            operations=None,
        ),
        compile_rule(
            effect="allow",
            methods=None,
            path="fetch:jentic/jentic-one-internal",
            match_mode="git",
            operations=None,
        ),
    ]
    repo = ("jentic", "jentic-one-internal")

    def allowed(item: GitItem) -> bool:
        idx = first_matching_rule(rules, method="", path="", operation_id=None, git_item=item)
        return rule_verdict(rules, idx) == "allow"

    assert allowed(GitItem(GitAction.FETCH, *repo))
    assert allowed(GitItem(GitAction.PUSH, *repo))  # the advertisement before a push
    assert allowed(GitItem(GitAction.PUSH, *repo, "refs/heads/jentic-e2e/a/b"))
    assert not allowed(GitItem(GitAction.PUSH, *repo, "refs/heads/main"))
    assert not allowed(GitItem(GitAction.PUSH, *repo, "refs/tags/v1"))
    assert not allowed(GitItem(GitAction.FORCE, *repo, "refs/heads/jentic-e2e/a"))
    assert not allowed(GitItem(GitAction.FETCH, "jentic", "other"))
