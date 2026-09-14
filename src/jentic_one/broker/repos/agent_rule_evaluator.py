"""Direct-binding permission-rule evaluator (theme-5 Phase 2).

The direct-binding twin of ``rule_evaluator``: queries ``agent_permission_rules``
keyed on ``(agent_id, credential_id)`` — or, when the binding carries a
``rule_set_id``, the shared ``permission_rule_set_rules`` list **instead** —
from the control DB (raw SQL; the broker cannot import control ORM) and
evaluates the ordered rule list against the inbound request. First-match-wins;
an exhausted rule list defaults to DENY (secure-by-default).

Unlike the toolkit evaluator there is **no vendor pooling**: rules are evaluated
strictly against the specific ``(agent, credential)`` binding (or its attached
rule set). The credential's identity was already matched during derivation, so
no vendor join is needed here.

Rule compilation and evaluation semantics are shared with the toolkit
evaluator (``PermissionRule``, ``evaluate_rules``, the JSON-column coercion and
the fail-closed path compilation), so the two enforcers cannot drift apart.

Performance: the rule list per binding (or per rule set — shared across N
bindings) is short-TTL cached (LRU + single-flight), amortising the hot-path DB
hit across requests.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog
from sqlalchemy import text

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.broker.repos.rule_evaluator import (
    DEFAULT_MAX_CACHE_ENTRIES,
    DEFAULT_RULE_CACHE_TTL_SECONDS,
    PermissionRule,
    _coerce_json_list,
    _normalize_methods,
    evaluate_rules,
)
from jentic_one.shared.broker.protocols import RuleEvaluation
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.permissions.matching import PathMatcher, compile_matcher

_logger = structlog.get_logger(__name__)

# Inline per-binding rules. No credential/vendor join — the binding is the key.
_BINDING_RULES_QUERY = text(
    "SELECT effect, methods, path, operations, match_mode "
    "FROM agent_permission_rules "
    "WHERE agent_id = :agent_id AND credential_id = :credential_id "
    "ORDER BY sequence ASC"
)

# Shared rule-set rules — evaluated *instead of* the inline rows when the
# binding carries a rule_set_id (the attach API enforces this precedence).
_RULE_SET_RULES_QUERY = text(
    "SELECT effect, methods, path, operations, match_mode "
    "FROM permission_rule_set_rules "
    "WHERE rule_set_id = :rule_set_id "
    "ORDER BY sequence ASC"
)


def _compile_path(raw: str | None, mode: str, *, binding: str) -> PathMatcher | None:
    """Compile a stored path pattern; log-once on a fail-closed row.

    Mirrors the toolkit evaluator's compilation: an unparseable stored pattern
    (a legacy row predating save-time validation) yields a matcher that never
    matches — fail-closed, never a silent wildcard (#751) — and the warning
    identifies the misconfigured binding/rule set so an operator can fix it.
    """
    matcher = compile_matcher(raw, mode)
    if matcher is not None and matcher.never:
        _logger.warning(
            "Ignoring direct-binding permission rule with an invalid stored path "
            "pattern (fail-closed — the rule never matches); fix the pattern to "
            "restore intent",
            binding=binding,
            path=raw,
            match_mode=mode,
        )
    return matcher


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
    ) -> RuleEvaluation:
        """Evaluate the binding's (or its rule set's) rules for the request.

        Returns a :class:`RuleEvaluation` — ``allowed`` plus the rule count so
        the router can distinguish "no rules configured for this binding"
        (rules_loaded == 0) from "loaded but nothing matched" in the deny
        problem detail (#578 twin).
        """
        rules = await self._get_rules(
            agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
        )
        if not rules:
            return RuleEvaluation(allowed=False, rules_loaded=0)
        allowed = evaluate_rules(
            rules,
            method=method,
            path=path,
            operation_id=operation_id,
        )
        return RuleEvaluation(allowed=allowed, rules_loaded=len(rules))

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
            binding_label = f"rule_set:{rule_set_id}"
        else:
            query = _BINDING_RULES_QUERY
            params = {"agent_id": agent_id, "credential_id": credential_id}
            binding_label = f"{agent_id}:{credential_id}"
        async with self._control_db.session() as session:
            rows = (await session.execute(query, params)).all()
        return [
            PermissionRule(
                effect=row[0],
                methods=_normalize_methods(_coerce_json_list(row[1])),
                path=_compile_path(row[2], str(row[4] or "regex"), binding=binding_label),
                operations=(tuple(ops) if (ops := _coerce_json_list(row[3])) is not None else None),
            )
            for row in rows
        ]

    def _store(self, key: str, entry: _CacheEntry) -> None:
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._cache.clear()
