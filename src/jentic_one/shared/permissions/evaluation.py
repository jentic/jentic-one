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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import structlog

from jentic_one.shared.permissions.matching import PathMatcher
from jentic_one.shared.url_path import normalize_path

_logger = structlog.get_logger(__name__)


def rule_request_path(relative_path: str | None, upstream_url: str) -> str:
    """The path binding rules are evaluated on for one request.

    ``relative_path`` is discovery's server-relative operation path
    (``OperationInfo.relative_path``). When discovery could not rebuild it —
    or an in-flight job payload predates it — the normalized full upstream
    path is the fallback: it can only match the rules a full-path rule would,
    never more, so the fallback is never broader than matching on the raw URL.
    """
    if relative_path is not None:
        return relative_path
    _logger.info("rule_path_fallback_full_upstream_path")
    return normalize_path(urlparse(upstream_url).path or "/")


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """A single compiled permission rule — immutable value object for cache safety."""

    effect: str
    methods: frozenset[str] | None
    path: PathMatcher | None
    operations: tuple[str, ...] | None


def normalize_methods(raw: Sequence[str] | None) -> frozenset[str] | None:
    """Upper-case a stored method list; ``None`` means "any method"."""
    if raw is None:
        return None
    return frozenset(m.upper() for m in raw)


def is_condition_less(rule: PermissionRule) -> bool:
    """True if a rule constrains nothing — matches every request when evaluated."""
    return rule.methods is None and rule.path is None and rule.operations is None


def rule_matches(rule: PermissionRule, *, method: str, path: str, operation_id: str | None) -> bool:
    """Return True if ALL defined criteria in the rule match the request."""
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
) -> int | None:
    """Index of the rule that decides the request, or ``None`` (default-deny).

    ``binding`` labels the rule source (``agent:credential`` ids or
    ``rule_set:<id>`` — identifiers only, never secret material) so a
    misconfiguration warning names what to fix.
    """
    for idx, rule in enumerate(rules):
        # Defense-in-depth: a condition-less `allow` is an unrestricted grant
        # (matches everything) and should have been rejected at the API schema.
        # If one reaches evaluation it is a misconfiguration — skip it rather
        # than honour blanket access. A condition-less `deny` keeps its
        # legitimate match-all catch-all behaviour.
        if is_condition_less(rule) and rule.effect.lower() == "allow":
            _logger.warning(
                "Ignoring misconfigured condition-less 'allow' permission rule "
                "(matches all requests); skipping to next rule",
                binding=binding,
            )
            continue
        if rule_matches(rule, method=method, path=path, operation_id=operation_id):
            return idx
    return None


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
    return idx is not None and rules[idx].effect.lower() == "allow"
