"""Permission-rule evaluation (single source of truth).

The ordered, first-match-wins evaluation of a binding's rule list. The broker
(``broker/repos/agent_rule_evaluator.py``) enforces with it and the control
dry-run (``permissions:test``) previews with it, so the two answers cannot
drift: a rule the dry-run reports as the match is the rule the broker applies.

The ``path`` every caller passes is the spec-relative request path — the
concrete path relative to the API's server URL, normalized by
``shared.url_path.normalize_path`` (``/widgets/42``, never ``/eu/widgets/42``
for a server ``https://host/{region}``). Rules are authored against the spec's
paths, so that is the only basis on which preview and enforcement agree
(#1424).

A GraphQL request is evaluated once per top-level field: callers pass the
field as ``graphql_field``, and only then can a ``match_mode="graphql"`` rule
match (see ``shared.permissions.graphql_rules``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

import structlog

from jentic_one.shared.permissions.graphql_rules import (
    GRAPHQL_MATCH_MODE,
    GraphQLField,
    GraphQLSelector,
    parse_selector,
)
from jentic_one.shared.permissions.matching import PathMatcher, compile_matcher
from jentic_one.shared.url_path import normalize_path

_logger = structlog.get_logger(__name__)


def rule_request_path(
    relative_path: str | None, upstream_url: str, *, operation_id: str | None = None
) -> str:
    """The path binding rules are evaluated on for one request.

    ``relative_path`` is discovery's server-relative operation path
    (``OperationInfo.relative_path``). When discovery could not rebuild it —
    or an in-flight job payload predates it — the normalized full upstream
    path is the fallback: it can only match the rules a full-path rule would,
    never more, so the fallback is never broader than matching on the raw URL.
    """
    if relative_path is not None:
        return relative_path
    _logger.info(
        "rule_path_fallback_full_upstream_path",
        operation_id=operation_id,
        upstream_host=urlparse(upstream_url).hostname,
    )
    return full_upstream_rule_path(upstream_url)


def full_upstream_rule_path(upstream_url: str) -> str:
    """The normalized full upstream path, server base path included.

    The basis rules were matched on before #1424 — the fallback of
    :func:`rule_request_path`, and the path the broker re-checks a denial
    against to flag a rule still written with the server's base path.
    """
    return normalize_path(urlparse(upstream_url).path or "/")


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """A single compiled permission rule — immutable value object for cache safety."""

    effect: str
    methods: frozenset[str] | None
    path: PathMatcher | None
    operations: tuple[str, ...] | None
    # The rule row's primary key, so a ``require-approval`` hold can record
    # which rule held the call.
    rule_id: str | None = None
    # A ``match_mode="graphql"`` rule's selector: the rule then matches only a
    # GraphQL request's top-level field it covers (``path`` is ``None``).
    graphql: GraphQLSelector | None = None


#: A path matcher that matches nothing: the fail-closed stand-in for a stored
#: pattern or selector that does not compile.
_NEVER = PathMatcher(mode="regex", literal=None, pattern=None, never=True)


def compile_rule(
    *,
    effect: str,
    methods: Sequence[str] | None,
    path: str | None,
    match_mode: str | None,
    operations: Sequence[str] | None,
    rule_id: str | None = None,
) -> PermissionRule:
    """Compile one stored rule row (the broker enforcer and the control dry-run share it).

    A ``graphql`` rule's ``path`` is its selector; one that does not parse
    compiles to a rule that never matches (fail closed), like an invalid
    stored path pattern.
    """
    mode = match_mode or "regex"
    if mode == GRAPHQL_MATCH_MODE:
        selector = parse_selector(path)
        return PermissionRule(
            effect=effect,
            methods=normalize_methods(methods),
            path=_NEVER if selector is None else None,
            operations=tuple(operations) if operations is not None else None,
            rule_id=rule_id,
            graphql=selector,
        )
    return PermissionRule(
        effect=effect,
        methods=normalize_methods(methods),
        path=compile_matcher(path, mode),
        operations=tuple(operations) if operations is not None else None,
        rule_id=rule_id,
    )


#: Effects a rule may only carry when it constrains methods, path, or operations.
CONDITION_REQUIRED_EFFECTS = frozenset({"allow", "require-approval"})


def normalize_methods(raw: Sequence[str] | None) -> frozenset[str] | None:
    """Upper-case a stored method list; ``None`` means "any method"."""
    if raw is None:
        return None
    return frozenset(m.upper() for m in raw)


def is_condition_less(rule: PermissionRule) -> bool:
    """True if a rule constrains nothing — matches every request when evaluated."""
    return (
        rule.methods is None
        and rule.path is None
        and rule.operations is None
        and rule.graphql is None
    )


def rule_matches(
    rule: PermissionRule,
    *,
    method: str,
    path: str,
    operation_id: str | None,
    graphql_field: GraphQLField | None = None,
) -> bool:
    """Return True if ALL defined criteria in the rule match the request.

    A GraphQL rule matches only when ``graphql_field`` (one top-level field of
    a GraphQL request) is given and its selector covers it.
    """
    if rule.graphql is not None and (
        graphql_field is None or not rule.graphql.matches(graphql_field)
    ):
        return False
    if rule.methods is not None and method.upper() not in rule.methods:
        return False
    if rule.path is not None and not rule.path.matches(path):
        return False
    if rule.operations is not None:
        return operation_id is not None and operation_id in rule.operations
    return True


def first_matching_rule(
    rules: Sequence[PermissionRule],
    *,
    method: str,
    path: str,
    operation_id: str | None,
    binding: str | None = None,
    warn: bool = True,
    graphql_field: GraphQLField | None = None,
) -> int | None:
    """Index of the rule that decides the request, or ``None`` (default-deny).

    ``graphql_field`` is the top-level field being evaluated for a GraphQL
    request (one call per field); ``None`` for any other request.

    ``binding`` labels the rule source (``agent:credential`` ids or
    ``rule_set:<id>`` — identifiers only, never secret material) so a
    misconfiguration warning names what to fix. ``warn=False`` silences that
    warning for a diagnostic re-evaluation of the same rule list.
    """
    for idx, rule in enumerate(rules):
        # Defense-in-depth: a condition-less `allow` would grant blanket access
        # and a condition-less `require-approval` would hold every call for
        # review. The API schema rejects both, so one reaching evaluation is a
        # misconfiguration — skip it rather than honour it. A condition-less
        # `deny` keeps its legitimate match-all catch-all behaviour.
        effect = rule.effect.lower()
        if is_condition_less(rule) and effect in CONDITION_REQUIRED_EFFECTS:
            if warn:
                _logger.warning(
                    "Ignoring misconfigured condition-less 'allow' or 'require-approval' "
                    "permission rule (matches all requests); skipping to next rule",
                    effect=effect,
                    binding=binding,
                )
            continue
        if rule_matches(
            rule, method=method, path=path, operation_id=operation_id, graphql_field=graphql_field
        ):
            return idx
    return None


def is_allowed(rules: Sequence[PermissionRule], idx: int | None) -> bool:
    """Whether a :func:`first_matching_rule` result runs the call straight away."""
    return idx is not None and rules[idx].effect.lower() == "allow"


def rule_verdict(rules: Sequence[PermissionRule], idx: int | None) -> str:
    """The effect a :func:`first_matching_rule` result applies.

    ``allow`` or ``require-approval`` from the deciding rule; ``deny`` for an
    explicit deny, an unknown effect, or no match (default-deny).
    """
    if idx is None:
        return "deny"
    effect = rules[idx].effect.lower()
    return effect if effect in CONDITION_REQUIRED_EFFECTS else "deny"


def evaluate_rules(
    rules: Sequence[PermissionRule],
    *,
    method: str,
    path: str,
    operation_id: str | None,
    binding: str | None = None,
) -> bool:
    """Evaluate an ordered list of permission rules. Returns True if allowed."""
    idx = first_matching_rule(
        rules, method=method, path=path, operation_id=operation_id, binding=binding
    )
    return is_allowed(rules, idx)


class DivergenceKind(StrEnum):
    """How a base-path-qualified rule changes a verdict after #1424."""

    #: An ``allow`` rule matches the full upstream path but not the
    #: spec-relative one — the request is now denied (fail-closed).
    LEGACY_ALLOW_NO_LONGER_MATCHES = "legacy_allow_no_longer_matches"
    #: A ``deny`` rule matches the full upstream path but not the
    #: spec-relative one — the request is now **allowed** by a later rule.
    LEGACY_DENY_NO_LONGER_MATCHES = "legacy_deny_no_longer_matches"
    #: A ``require-approval`` rule matches the full upstream path but not the
    #: spec-relative one — the request is no longer held for approval.
    LEGACY_ASK_NO_LONGER_MATCHES = "legacy_ask_no_longer_matches"


@dataclass(frozen=True, slots=True)
class PathDivergence:
    """A verdict that differs between the spec-relative and full upstream path
    **because of** one rule written against the full upstream path."""

    kind: DivergenceKind
    rule_index: int
    rule_path: str | None


def _matcher_source(matcher: PathMatcher | None) -> str | None:
    if matcher is None:
        return None
    if matcher.literal is not None:
        return matcher.literal
    return matcher.pattern.pattern if matcher.pattern is not None else None


def base_path_divergence(
    rules: Sequence[PermissionRule],
    *,
    method: str,
    relative_path: str,
    upstream_path: str,
    operation_id: str | None,
) -> PathDivergence | None:
    """Explain a verdict change caused by a rule carrying the server base path.

    Evaluates the rule list on both paths. Returns a divergence only when the
    verdicts differ **and** the rule that decides the full-upstream-path
    verdict does not match the spec-relative path — i.e. the rule itself is
    written with the base path. A spec-relative rule list whose verdicts
    differ merely because the upstream path is a different string (``deny
    /widgets`` then ``allow /``) is not flagged.
    """
    if relative_path == upstream_path:
        return None
    rel_idx = first_matching_rule(
        rules, method=method, path=relative_path, operation_id=operation_id, warn=False
    )
    up_idx = first_matching_rule(
        rules, method=method, path=upstream_path, operation_id=operation_id, warn=False
    )
    if up_idx is None or rule_verdict(rules, rel_idx) == rule_verdict(rules, up_idx):
        return None
    rule = rules[up_idx]
    if rule_matches(rule, method=method, path=relative_path, operation_id=operation_id):
        return None
    effect = rule.effect.lower()
    if effect == "allow":
        kind = DivergenceKind.LEGACY_ALLOW_NO_LONGER_MATCHES
    elif effect == "require-approval":
        kind = DivergenceKind.LEGACY_ASK_NO_LONGER_MATCHES
    else:
        kind = DivergenceKind.LEGACY_DENY_NO_LONGER_MATCHES
    return PathDivergence(kind=kind, rule_index=up_idx, rule_path=_matcher_source(rule.path))
