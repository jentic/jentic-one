"""Direct-binding permission-rule evaluator (theme-5 Phase 2).

Queries ``agent_permission_rules``
keyed on ``(agent_id, credential_id)`` — or, when the binding carries a
``rule_set_id``, the shared ``permission_rule_set_rules`` list **instead** —
from the control DB (raw SQL; the broker cannot import control ORM) and
evaluates the ordered rule list against the inbound request. First-match-wins;
an exhausted rule list defaults to DENY (secure-by-default).

Unlike the deleted toolkit evaluator (pre-6b ``rule_evaluator``) there is **no
vendor pooling**: rules are evaluated
strictly against the specific ``(agent, credential)`` binding (or its attached
rule set). The credential's identity was already matched during derivation, so
no vendor join is needed here.

A GraphQL request (``graphql``) is evaluated once per top-level field and the
most restrictive field verdict decides (``shared.permissions.graphql_rules``).

Path matching (``regex``/``prefix``/``exact``) delegates to the shared
``shared.permissions.matching`` seam and the first-match-wins loop to
``shared.permissions.evaluation``, so the control dry-run
(``permissions:test``) and this enforcer cannot disagree. This module owns the
DB read, JSON-column coercion and the rule cache.

Performance: the rule list per binding (or per rule set — shared across N
bindings) is short-TTL cached (LRU + single-flight), amortising the hot-path DB
hit across requests.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import text

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.broker.protocols import (
    GraphQLFieldEvaluation,
    RuleEvaluation,
    RuleVerdict,
)
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    base_path_divergence,
    compile_rule,
    first_matching_rule,
    rule_verdict,
)
from jentic_one.shared.permissions.graphql_document import GraphQLCall
from jentic_one.shared.permissions.graphql_rules import (
    FieldVerdict,
    GraphQLField,
    combine_field_verdicts,
)

_logger = structlog.get_logger(__name__)

DEFAULT_RULE_CACHE_TTL_SECONDS = 30.0
DEFAULT_MAX_CACHE_ENTRIES = 5_000


def _coerce_json_list(value: object) -> list[str] | None:
    """Coerce a JSON column value into a list of strings (or None).

    The evaluator reads rules via raw ``text()`` SQL, which bypasses the ORM's
    ``json_variant()`` deserialization. On PostgreSQL the JSONB driver still
    decodes the column into native lists, but on SQLite (JSON stored as TEXT)
    the raw string comes straight through — e.g. ``'["GET", "POST"]'`` or the
    literal ``'null'``. Parse the string form here so both backends yield the
    same list; a non-string list (already decoded) passes through unchanged.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


# Inline per-binding rules. No credential/vendor join — the binding is the key.
# ``id`` is selected so the hold path can record which rule triggered the hold
# as ``matched_rule_id`` on the ``held_executions`` row.
_BINDING_RULES_QUERY = text(
    "SELECT id, effect, methods, path, operations, match_mode "
    "FROM agent_permission_rules "
    "WHERE agent_id = :agent_id AND credential_id = :credential_id "
    "ORDER BY sequence ASC"
)

# Shared rule-set rules — evaluated *instead of* the inline rows when the
# binding carries a rule_set_id (the attach API enforces this precedence).
_RULE_SET_RULES_QUERY = text(
    "SELECT id, effect, methods, path, operations, match_mode "
    "FROM permission_rule_set_rules "
    "WHERE rule_set_id = :rule_set_id "
    "ORDER BY sequence ASC"
)


def _binding_label(*, agent_id: str, credential_id: str, rule_set_id: str | None) -> str:
    """Log label for a rule source: the shared set, or the ``agent:credential`` binding."""
    if rule_set_id is not None:
        return f"rule_set:{rule_set_id}"
    return f"{agent_id}:{credential_id}"


def _compile_row(row: Sequence[Any], *, binding: str) -> PermissionRule:
    """Compile one stored rule row; log-once on a fail-closed row.

    Delegates to the shared seam: an unparseable stored pattern or GraphQL
    selector (a row predating save-time validation) yields a rule that never
    matches — fail-closed, never a silent wildcard (#751) — and the warning
    identifies the misconfigured binding/rule set so an operator can fix it.
    """
    # Row columns: id(0), effect(1), methods(2), path(3), operations(4), match_mode(5)
    mode = str(row[5] or "regex")
    rule = compile_rule(
        effect=row[1],
        methods=_coerce_json_list(row[2]),
        path=row[3],
        match_mode=mode,
        operations=_coerce_json_list(row[4]),
        rule_id=str(row[0]) if row[0] is not None else None,
    )
    if rule.path is not None and rule.path.never:
        _logger.warning(
            "Ignoring direct-binding permission rule with an invalid stored path "
            "pattern (fail-closed — the rule never matches); fix the pattern to "
            "restore intent",
            binding=binding,
            path=row[3],
            match_mode=mode,
        )
    return rule


def _evaluate_graphql(
    rules: list[PermissionRule],
    graphql: GraphQLCall,
    *,
    method: str,
    path: str,
    operation_id: str | None,
    binding: str,
) -> RuleEvaluation:
    """Evaluate the rule list once per top-level field; the most restrictive field decides."""
    verdicts: list[FieldVerdict] = []
    for position, name in enumerate(graphql.top_level_fields):
        idx = first_matching_rule(
            rules,
            method=method,
            path=path,
            operation_id=operation_id,
            binding=binding,
            # One misconfiguration warning per request, not per field.
            warn=position == 0,
            graphql_field=GraphQLField(operation_type=graphql.operation_type, name=name),
        )
        verdicts.append(FieldVerdict(field=name, verdict=rule_verdict(rules, idx), rule_index=idx))
    deciding = combine_field_verdicts(verdicts)
    fields = tuple(
        GraphQLFieldEvaluation(
            field=v.field,
            verdict=RuleVerdict(v.verdict),
            matched_rule_id=rules[v.rule_index].rule_id if v.rule_index is not None else None,
        )
        for v in verdicts
    )
    if deciding is None:
        # A parsed document always selects at least one field; fail closed.
        return RuleEvaluation(verdict=RuleVerdict.DENY, rules_loaded=len(rules))
    return RuleEvaluation(
        verdict=RuleVerdict(deciding.verdict),
        rules_loaded=len(rules),
        matched_rule_id=(
            rules[deciding.rule_index].rule_id if deciding.rule_index is not None else None
        ),
        graphql_fields=fields,
    )


@dataclass(slots=True)
class _CacheEntry:
    """A cached rule list with its insertion time (monotonic)."""

    rules: list[PermissionRule]
    cached_at: float


class AgentRuleEvaluator:
    """Evaluates direct-binding permission rules with TTL-LRU caching.

    Implements ``AgentRuleEvaluatorProtocol``. Cache keys are per binding
    (``agent_id, credential_id``) for inline rules and per ``rule_set_id`` for
    shared sets — a set attached to N bindings is fetched once, not N times.
    A cache hit within ``cache_ttl_seconds`` returns the cached rule list
    without touching the DB; concurrent misses for the same key are coalesced
    via single-flight.
    """

    def __init__(
        self,
        control_db: DatabaseSession,
        *,
        cache_ttl_seconds: float = DEFAULT_RULE_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._control_db = control_db
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._single_flight: SingleFlight[list[PermissionRule]] = SingleFlight()

    async def evaluate(
        self,
        *,
        agent_id: str,
        credential_id: str,
        rule_set_id: str | None,
        method: str,
        path: str,
        operation_id: str | None,
        upstream_path: str | None = None,
        graphql: GraphQLCall | None = None,
    ) -> RuleEvaluation:
        """Evaluate the binding's (or its rule set's) rules for the request.

        Returns a :class:`RuleEvaluation` carrying the tri-state verdict, the
        rule count (so the router can distinguish "no rules configured for this
        binding" (rules_loaded == 0) from "loaded but nothing matched" in the
        deny problem detail — #578 twin) and the matched rule id for the hold
        path. With ``upstream_path`` (the normalized full upstream path) it also
        reports a verdict change caused by a rule still written with the server
        base path; that never alters the verdict.

        With ``graphql`` the rules are evaluated once per top-level field
        (``graphql_fields`` on the result) and the most restrictive field
        verdict is the request's.
        """
        rules = await self._get_rules(
            agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
        )
        if not rules:
            return RuleEvaluation(verdict=RuleVerdict.DENY, rules_loaded=0)
        binding = _binding_label(
            agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
        )
        if graphql is not None:
            return _evaluate_graphql(
                rules,
                graphql,
                method=method,
                path=path,
                operation_id=operation_id,
                binding=binding,
            )
        idx = first_matching_rule(
            rules,
            method=method,
            path=path,
            operation_id=operation_id,
            binding=binding,
        )
        divergence = (
            base_path_divergence(
                rules,
                method=method,
                relative_path=path,
                upstream_path=upstream_path,
                operation_id=operation_id,
            )
            if upstream_path is not None
            else None
        )
        return RuleEvaluation(
            verdict=RuleVerdict(rule_verdict(rules, idx)),
            rules_loaded=len(rules),
            matched_rule_id=rules[idx].rule_id if idx is not None else None,
            divergence=divergence,
        )

    async def _get_rules(
        self, *, agent_id: str, credential_id: str, rule_set_id: str | None
    ) -> list[PermissionRule]:
        """Fetch rules from cache or DB (single-flighted)."""
        # NUL-joined so component boundaries are unambiguous; the "set" prefix
        # keeps a rule-set key from ever colliding with a binding key.
        cache_key = (
            f"set\x00{rule_set_id}"
            if rule_set_id is not None
            else f"binding\x00{agent_id}\x00{credential_id}"
        )
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and (now - cached.cached_at) < self._cache_ttl_seconds:
            self._cache.move_to_end(cache_key)
            return cached.rules

        async def _load() -> list[PermissionRule]:
            rules = await self._fetch_rules(
                agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
            )
            self._store(cache_key, _CacheEntry(rules=rules, cached_at=time.monotonic()))
            return rules

        return await self._single_flight.do(cache_key, _load)

    async def _fetch_rules(
        self, *, agent_id: str, credential_id: str, rule_set_id: str | None
    ) -> list[PermissionRule]:
        """Load the binding's rules (or its rule set's) from the control DB."""
        if rule_set_id is not None:
            query = _RULE_SET_RULES_QUERY
            params: dict[str, str] = {"rule_set_id": rule_set_id}
        else:
            query = _BINDING_RULES_QUERY
            params = {"agent_id": agent_id, "credential_id": credential_id}
        binding_label = _binding_label(
            agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
        )
        async with self._control_db.session() as session:
            rows = (await session.execute(query, params)).all()
        return [_compile_row(row, binding=binding_label) for row in rows]

    def _store(self, key: str, entry: _CacheEntry) -> None:
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def invalidate_binding(self, *, agent_id: str, credential_id: str) -> None:
        """Evict cached inline rules for ``(agent_id, credential_id)`` (#1837)."""
        self._cache.pop(f"binding\x00{agent_id}\x00{credential_id}", None)

    def invalidate_agent(self, agent_id: str) -> None:
        """Evict all cached inline rules for ``agent_id`` (#1837)."""
        prefix = f"binding\x00{agent_id}\x00"
        stale = [k for k in self._cache if k.startswith(prefix)]
        for k in stale:
            self._cache.pop(k, None)

    def invalidate_credential(self, credential_id: str) -> None:
        """Evict all cached inline rules for ``credential_id`` (#1837)."""
        suffix = f"\x00{credential_id}"
        stale = [k for k in self._cache if k.startswith("binding\x00") and k.endswith(suffix)]
        for k in stale:
            self._cache.pop(k, None)

    def invalidate_rule_set(self, rule_set_id: str) -> None:
        """Evict cached shared rule-set rules for ``rule_set_id`` (#1837)."""
        self._cache.pop(f"set\x00{rule_set_id}", None)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._cache.clear()
