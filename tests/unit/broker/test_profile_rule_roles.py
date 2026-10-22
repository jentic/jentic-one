"""Rules on a profile-scoped credential's binding apply to the API of their role only.

One binding carries rules for every API of a CLI profile (GitHub: REST,
GraphQL, git). The broker evaluates only the rules of the requested API's role
(``rules_for_api_role``), so a REST path rule never decides a GraphQL field or
a git push, and an API with no rule of its own is denied by default.
"""

from __future__ import annotations

from typing import Any

import pytest

from jentic_one.broker.repos.agent_rule_evaluator import _compile_row, _evaluate_graphql
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import binding_api_role
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.permissions.api_roles import default_api_role, effective_api_role
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    first_matching_rule,
    rule_verdict,
    rules_for_api_role,
)
from jentic_one.shared.permissions.git_rules import GitAction, GitItem
from jentic_one.shared.permissions.graphql_document import GraphQLCall
from jentic_one.shared.schemas import APIReference


def _rules(*rows: tuple[str, list[str] | None, str, str, str | None]) -> list[PermissionRule]:
    """``(effect, methods, path, match_mode, api_role)`` rows compiled as the broker does."""
    return [
        _compile_row((f"apr_{i}", effect, methods, path, None, mode, role), binding="agt:cred")
        for i, (effect, methods, path, mode, role) in enumerate(rows)
    ]


#: The read_and_push preset for one repository, as connect stores it.
_PROFILE_RULES = _rules(
    ("allow", ["GET"], "/", "prefix", "rest"),
    ("allow", None, "query", "graphql", "graphql"),
    ("deny", None, "mutation", "graphql", "graphql"),
    ("allow", None, "fetch:octo-org/app", "git", "git"),
    ("allow", None, "push:octo-org/app:refs/heads/agent/*", "git", "git"),
)


def _rest(rules: list[PermissionRule], method: str, path: str) -> str:
    scoped = rules_for_api_role(rules, "rest")
    return rule_verdict(
        scoped, first_matching_rule(scoped, method=method, path=path, operation_id=None)
    )


def _git(rules: list[PermissionRule], item: GitItem) -> str:
    scoped = rules_for_api_role(rules, "git")
    idx = first_matching_rule(scoped, method="", path="", operation_id=None, git_item=item)
    return rule_verdict(scoped, idx)


def _graphql(rules: list[PermissionRule], operation_type: str, field: str) -> RuleVerdict:
    return _evaluate_graphql(
        rules_for_api_role(rules, "graphql"),
        GraphQLCall(operation_type=operation_type, top_level_fields=(field,)),  # type: ignore[arg-type]
        method="POST",
        path="/graphql",
        operation_id="graphql",
        binding="agt:cred",
    ).verdict


@pytest.mark.parametrize(
    ("mode", "role"),
    [("git", "git"), ("graphql", "graphql"), ("regex", "rest"), ("prefix", "rest"), (None, "rest")],
)
def test_default_role_is_the_rules_kind(mode: str | None, role: str) -> None:
    assert default_api_role(mode) == role
    assert effective_api_role(None, mode) == role
    assert effective_api_role("uploads", mode) == "uploads"


def test_a_stored_rule_without_a_role_takes_its_kind() -> None:
    (rule,) = _rules(("allow", None, "fetch:o/r", "git", None))
    assert rule.api_role == "git"
    (legacy,) = [_compile_row(("apr_x", "allow", ["GET"], "/", None, "prefix"), binding="b")]
    assert legacy.api_role == "rest"


def test_rest_gets_its_path_rules_only() -> None:
    assert _rest(_PROFILE_RULES, "GET", "/repos/octo-org/app") == "allow"
    assert _rest(_PROFILE_RULES, "POST", "/repos/octo-org/app/issues") == "deny"


def test_graphql_gets_its_graphql_rules_only() -> None:
    assert _graphql(_PROFILE_RULES, "query", "repository") == RuleVerdict.ALLOW
    assert _graphql(_PROFILE_RULES, "mutation", "createPullRequest") == RuleVerdict.DENY


def test_a_rest_path_rule_never_decides_a_graphql_field() -> None:
    # On a non-profile credential a plain path rule matches every GraphQL
    # field; on a profile credential it is a REST rule and is not evaluated.
    rules = _rules(("allow", ["POST"], "/graphql", "exact", None))
    assert _graphql(rules, "mutation", "createPullRequest") == RuleVerdict.DENY
    assert _graphql(rules, "query", "viewer") == RuleVerdict.DENY


def test_git_gets_its_git_rules_only() -> None:
    fetch = GitItem(GitAction.FETCH, "octo-org", "app")
    push_ok = GitItem(GitAction.PUSH, "octo-org", "app", "refs/heads/agent/fix")
    push_main = GitItem(GitAction.PUSH, "octo-org", "app", "refs/heads/main")
    assert _git(_PROFILE_RULES, fetch) == "allow"
    assert _git(_PROFILE_RULES, push_ok) == "allow"
    assert _git(_PROFILE_RULES, push_main) == "deny"
    assert _git(_PROFILE_RULES, GitItem(GitAction.FETCH, "octo-org", "other")) == "deny"


def test_an_api_with_no_rule_of_its_own_is_denied_by_default() -> None:
    rest_only = _rules(("allow", ["GET", "POST"], "/", "prefix", None))
    assert rules_for_api_role(rest_only, "graphql") == []
    assert rules_for_api_role(rest_only, "git") == []
    assert _graphql(rest_only, "query", "viewer") == RuleVerdict.DENY
    assert _git(rest_only, GitItem(GitAction.FETCH, "octo-org", "app")) == "deny"
    # A REST condition-less deny does not reach the git API either.
    deny_all = _rules(("deny", None, "/", "prefix", None))
    assert rules_for_api_role(deny_all, "git") == []


def _credential(cli_profile: str | None) -> ResolvedCredential:
    return ResolvedCredential(
        credential_id="cred_1",
        name="GitHub",
        cli_profile=cli_profile,
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_DEVICE_CODE,
        provider="device_authorization",
    )


@pytest.mark.parametrize(
    ("api", "role"),
    [
        ({"vendor": "github-com", "name": "api-github-com", "version": "1.1.4"}, "rest"),
        ({"vendor": "github-com", "name": "github-graphql", "version": "1"}, "graphql"),
        ({"vendor": "github-com", "name": "github-git", "version": "1"}, "git"),
    ],
)
def test_binding_role_comes_from_the_profile_manifest(api: dict[str, Any], role: str) -> None:
    assert binding_api_role(_credential("github"), APIReference(**api)) == role


def test_non_profile_credentials_evaluate_every_rule() -> None:
    api = APIReference(vendor="github-com", name="api-github-com", version="1.1.4")
    assert binding_api_role(_credential(None), api) is None


def test_an_undeclared_api_gets_a_role_no_rule_has() -> None:
    api = APIReference(vendor="github-com", name="gists", version="1")
    role = binding_api_role(_credential("github"), api)
    assert role is not None
    assert rules_for_api_role(_PROFILE_RULES, role) == []
