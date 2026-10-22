"""GraphQL top-level-field rules: selectors, matching, fail-closed storage, presets and policy."""

from __future__ import annotations

import pytest

from jentic_one.broker.core.exceptions import GraphQLRequestRejectedError
from jentic_one.broker.repos.agent_rule_evaluator import _compile_row, _evaluate_graphql
from jentic_one.broker.services.execution.authorization import (
    _hold_ref_mutations,
    require_graphql_inspection,
)
from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema
from jentic_one.shared.broker.protocols import (
    GraphQLFieldEvaluation,
    RuleEvaluation,
    RuleVerdict,
)
from jentic_one.shared.permissions.base_path_rewrite import decide_rewrite
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    compile_rule,
    evaluate_rules,
    first_matching_rule,
    normalize_methods,
)
from jentic_one.shared.permissions.github_graphql import (
    GITHUB_GRAPHQL_PRESETS,
    HARD_DENIED_MUTATIONS,
    HELD_MUTATIONS,
    hard_deny_reason,
    is_github_graphql_url,
)
from jentic_one.shared.permissions.graphql_document import GraphQLCall
from jentic_one.shared.permissions.graphql_rules import (
    FieldVerdict,
    GraphQLField,
    GraphQLSelector,
    combine_field_verdicts,
    parse_selector,
    selector_error,
)
from jentic_one.shared.permissions.matching import compile_matcher, validate_path

_GRAPHQL_PATH = "/graphql"


def _rules(*rows: tuple[str, str | None, str, list[str] | None]) -> list[PermissionRule]:
    """``(effect, path, match_mode, methods)`` rows compiled as the broker does."""
    return [
        _compile_row((f"apr_{i}", effect, methods, path, None, mode), binding="agt:cred")
        for i, (effect, path, mode, methods) in enumerate(rows)
    ]


def _evaluate(rules: list[PermissionRule], operation_type: str, *fields: str) -> RuleEvaluation:
    return _evaluate_graphql(
        rules,
        GraphQLCall(operation_type=operation_type, top_level_fields=fields),  # type: ignore[arg-type]
        method="POST",
        path=_GRAPHQL_PATH,
        operation_id="graphql",
        binding="agt:cred",
    )


# ── selectors ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("query", GraphQLSelector("query")),
        ("mutation", GraphQLSelector("mutation")),
        (
            "mutation:createPullRequest,addComment",
            GraphQLSelector("mutation", frozenset({"createPullRequest", "addComment"})),
        ),
        ("query:__typename", GraphQLSelector("query", frozenset({"__typename"}))),
    ],
)
def test_valid_selectors_compile(raw: str, expected: GraphQLSelector) -> None:
    assert selector_error(raw) is None
    assert parse_selector(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "subscription",
        "Mutation",
        "mutation:",
        "mutation:a, b",
        "mutation:a,,b",
        "mutation:1a",
        "mutation:a.b",
        "/graphql",
        "query:" + "a" * 1000,
    ],
)
def test_invalid_selectors_are_refused_and_fail_closed(raw: str | None) -> None:
    assert selector_error(raw) is not None
    assert parse_selector(raw) is None
    assert validate_path(raw, "graphql") is not None


def test_selector_matches_by_operation_type_and_exact_field_name() -> None:
    selector = GraphQLSelector("mutation", frozenset({"createPullRequest"}))

    assert selector.matches(GraphQLField("mutation", "createPullRequest"))
    assert not selector.matches(GraphQLField("mutation", "createpullrequest"))
    assert not selector.matches(GraphQLField("mutation", "deleteRef"))
    assert not selector.matches(GraphQLField("query", "createPullRequest"))
    assert GraphQLSelector("query").matches(GraphQLField("query", "anything"))


def test_most_restrictive_field_verdict_wins_and_ties_keep_document_order() -> None:
    verdicts = [
        FieldVerdict("a", "allow", 0),
        FieldVerdict("b", "require-approval", 1),
        FieldVerdict("c", "require-approval", 2),
    ]
    assert combine_field_verdicts(verdicts) == FieldVerdict("b", "require-approval", 1)
    assert combine_field_verdicts([*verdicts, FieldVerdict("d", "deny", None)]) == FieldVerdict(
        "d", "deny", None
    )
    assert combine_field_verdicts([]) is None


# ── storage shape: readers that do not know GraphQL rules fail closed ────────


@pytest.mark.parametrize("effect", ["allow", "require-approval"])
def test_a_path_only_reader_never_matches_a_graphql_rule(effect: str) -> None:
    """A reader that compiles ``(path, match_mode)`` as a path never matches a GraphQL rule."""
    path_only = PermissionRule(
        effect=effect,
        methods=normalize_methods(["POST"]),
        path=compile_matcher("mutation", "graphql"),
        operations=None,
    )
    allow_all_after = PermissionRule(effect="deny", methods=None, path=None, operations=None)

    for path in (_GRAPHQL_PATH, "mutation", "/"):
        idx = first_matching_rule(
            [path_only, allow_all_after], method="POST", path=path, operation_id=None
        )
        assert idx == 1


def test_graphql_rule_never_matches_a_non_graphql_request() -> None:
    rules = _rules(("allow", "query", "graphql", None), ("allow", "mutation", "graphql", None))

    assert not evaluate_rules(rules, method="POST", path=_GRAPHQL_PATH, operation_id=None)


def test_graphql_rule_is_not_condition_less_and_needs_a_selector() -> None:
    PermissionRuleSchema(effect="allow", path="query", match_mode="graphql")
    with pytest.raises(ValueError, match="selector"):
        PermissionRuleSchema(effect="deny", match_mode="graphql")
    with pytest.raises(ValueError, match="operation type"):
        PermissionRuleSchema(effect="allow", path="/graphql", match_mode="graphql")


def test_stored_invalid_selector_compiles_to_a_rule_that_never_matches() -> None:
    (rule,) = _rules(("allow", "mutation:a b", "graphql", None))

    assert rule.graphql is None
    assert rule.path is not None and rule.path.never
    assert _evaluate([rule], "mutation", "a").verdict == RuleVerdict.DENY


def test_base_path_rewrite_leaves_graphql_selectors_alone() -> None:
    assert decide_rewrite("mutation:addComment", "graphql", [None]).new_path is None


def test_dry_run_compile_matches_the_broker_compile() -> None:
    row = ("apr_1", "allow", ["POST"], "mutation:addComment", None, "graphql")

    shared = compile_rule(
        effect="allow",
        methods=["POST"],
        path="mutation:addComment",
        match_mode="graphql",
        operations=None,
        rule_id="apr_1",
    )

    assert _compile_row(row, binding="agt:cred") == shared


# ── evaluation per top-level field ───────────────────────────────────────────


def test_each_field_is_evaluated_and_the_most_restrictive_decides() -> None:
    rules = _rules(
        ("allow", "mutation:addComment", "graphql", None),
        ("require-approval", "mutation:createPullRequest", "graphql", None),
        ("deny", "mutation", "graphql", None),
    )

    held = _evaluate(rules, "mutation", "addComment", "createPullRequest")
    denied = _evaluate(rules, "mutation", "addComment", "deleteRef", "createPullRequest")

    assert (held.verdict, held.matched_rule_id) == (RuleVerdict.REQUIRE_APPROVAL, "apr_1")
    assert [f.verdict for f in held.graphql_fields] == [
        RuleVerdict.ALLOW,
        RuleVerdict.REQUIRE_APPROVAL,
    ]
    assert (denied.verdict, denied.matched_rule_id) == (RuleVerdict.DENY, "apr_2")
    assert [f.field for f in denied.graphql_fields] == [
        "addComment",
        "deleteRef",
        "createPullRequest",
    ]


def test_plain_path_rules_still_apply_to_every_field() -> None:
    rules = _rules(("allow", "/graphql", "exact", ["POST"]))

    assert _evaluate(rules, "mutation", "deleteRef", "addComment").verdict == RuleVerdict.ALLOW


def test_unmatched_field_is_denied_by_default() -> None:
    rules = _rules(("allow", "query:viewer", "graphql", None))

    result = _evaluate(rules, "query", "viewer", "repository")

    assert result.verdict == RuleVerdict.DENY
    assert result.matched_rule_id is None
    assert result.graphql_fields[1] == GraphQLFieldEvaluation("repository", RuleVerdict.DENY)


def test_methods_constrain_a_graphql_rule_too() -> None:
    rules = _rules(("allow", "query", "graphql", ["GET"]))

    assert _evaluate(rules, "query", "viewer").verdict == RuleVerdict.DENY


# ── presets ──────────────────────────────────────────────────────────────────


def _preset(name: str) -> list[PermissionRule]:
    rules = []
    for i, rule in enumerate(GITHUB_GRAPHQL_PRESETS[name]):
        schema = PermissionRuleSchema.model_validate(
            {k: v for k, v in rule.items() if not k.startswith("_")}
        )
        rules.append(
            compile_rule(
                effect=schema.effect,
                methods=schema.methods,
                path=schema.path,
                match_mode=schema.match_mode,
                operations=schema.operations,
                rule_id=f"preset_{i}",
            )
        )
    return rules


@pytest.mark.parametrize(
    ("preset", "operation_type", "field", "expected"),
    [
        ("read_only", "query", "viewer", RuleVerdict.ALLOW),
        ("read_only", "query", "__typename", RuleVerdict.ALLOW),
        ("read_only", "mutation", "createPullRequest", RuleVerdict.DENY),
        ("read_only", "mutation", "addComment", RuleVerdict.DENY),
        ("pr_write", "query", "repository", RuleVerdict.ALLOW),
        ("pr_write", "mutation", "createPullRequest", RuleVerdict.ALLOW),
        ("pr_write", "mutation", "mergePullRequest", RuleVerdict.REQUIRE_APPROVAL),
        ("pr_write_with_approval", "query", "viewer", RuleVerdict.ALLOW),
        ("pr_write_with_approval", "mutation", "createPullRequest", RuleVerdict.REQUIRE_APPROVAL),
        ("pr_write_with_approval", "mutation", "addComment", RuleVerdict.REQUIRE_APPROVAL),
        ("pr_write_with_approval", "mutation", "deleteRef", RuleVerdict.DENY),
    ],
)
def test_presets(preset: str, operation_type: str, field: str, expected: RuleVerdict) -> None:
    assert _evaluate(_preset(preset), operation_type, field).verdict == expected


def test_presets_are_valid_rule_lists_and_allow_no_held_or_hard_denied_field() -> None:
    for name in GITHUB_GRAPHQL_PRESETS:
        rules = _preset(name)
        for field in (*HARD_DENIED_MUTATIONS, *HELD_MUTATIONS):
            result = _hold_ref_mutations(_evaluate(rules, "mutation", field))
            assert result.verdict != RuleVerdict.ALLOW, (name, field)


# ── GitHub policy ────────────────────────────────────────────────────────────


def test_ref_mutations_allowed_by_a_rule_are_held_and_denials_stay() -> None:
    rules = _rules(
        ("deny", "mutation:deleteRef", "graphql", None),
        ("allow", "mutation", "graphql", None),
    )

    held = _hold_ref_mutations(_evaluate(rules, "mutation", "addComment", "createCommitOnBranch"))
    denied = _hold_ref_mutations(_evaluate(rules, "mutation", "updateRefs", "deleteRef"))

    assert held.verdict == RuleVerdict.REQUIRE_APPROVAL
    assert held.matched_rule_id == "apr_1"
    assert [f.verdict for f in held.graphql_fields] == [
        RuleVerdict.ALLOW,
        RuleVerdict.REQUIRE_APPROVAL,
    ]
    assert denied.verdict == RuleVerdict.DENY


def test_non_graphql_evaluation_is_left_alone_by_the_ref_hold() -> None:
    evaluation = RuleEvaluation(verdict=RuleVerdict.ALLOW, rules_loaded=1)

    assert _hold_ref_mutations(evaluation) is evaluation


@pytest.mark.parametrize(
    "field",
    [
        "createBranchProtectionRule",
        "updateBranchProtectionRule",
        "deleteBranchProtectionRule",
        "createRepositoryRuleset",
        "updateRepositoryRuleset",
        "deleteRepositoryRuleset",
        "updateTeamsRepository",
        "inviteEnterpriseMember",
        "createMigrationSource",
        "approveDeployments",
        "createIpAllowListEntry",
    ],
)
def test_hard_denied_fields_have_a_reason(field: str) -> None:
    assert hard_deny_reason(field)


@pytest.mark.parametrize("field", ["createPullRequest", "updateRepository", "addComment"])
def test_ordinary_mutations_are_not_hard_denied(field: str) -> None:
    assert hard_deny_reason(field) is None


def test_hard_denied_and_held_lists_do_not_overlap() -> None:
    assert not set(HARD_DENIED_MUTATIONS) & HELD_MUTATIONS


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/graphql",
        "https://api.github.com/graphql?x=1",
        "https://API.GitHub.com./graphql",
        "https://api.github.com:443/graphql",
        "http://api.github.com:80/graphql",
        "https://api.github.com/graphql/",
        "https://api.github.com//graphql",
        "https://api.github.com/GraphQL",
        "https://api.github.com/%67raphql",
        "https://api.github.com/x/../graphql",
    ],
)
def test_every_spelling_of_the_github_graphql_endpoint_is_recognised(url: str) -> None:
    assert is_github_graphql_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/o/r",
        "https://api.github.com/graphql/x",
        "https://uploads.github.com/graphql",
        "https://example.com/graphql",
        "https://api.github.com.evil.test/graphql",
        "not a url",
    ],
)
def test_other_urls_are_not_the_github_graphql_endpoint(url: str) -> None:
    assert not is_github_graphql_url(url)


def test_an_uninspected_github_graphql_call_is_refused() -> None:
    with pytest.raises(GraphQLRequestRejectedError):
        require_graphql_inspection("https://api.github.com/graphql", None, instance="/x")
    require_graphql_inspection(
        "https://api.github.com/graphql",
        GraphQLCall(operation_type="query", top_level_fields=("viewer",)),
        instance="/x",
    )
    require_graphql_inspection("https://api.github.com/repos/o/r", None, instance="/x")
