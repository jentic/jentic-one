"""Toolkit permission-rule evaluator — enforces row-level ALLOW/DENY rules.

Queries ``toolkit_permission_rules`` from the control DB (raw SQL — the broker
cannot import control ORM) and evaluates the ordered rule list against the
inbound request. A first-match-wins policy terminates on the first matching
rule's effect; an exhausted rule list defaults to DENY (secure-by-default).

Path matching (``regex``/``prefix``/``exact``) is delegated to the shared
``shared.permissions.matching`` seam so authoring surfaces and this enforcer
cannot disagree; a stored pattern that fails to compile at load time is
fail-closed (never matches) rather than the pre-#751 silent-wildcard.

Performance: the rule list per (toolkit, credential) pair is short-TTL cached
(LRU + single-flight) so the hot-path DB hit is amortised across requests.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog
from sqlalchemy import text

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.broker.protocols import RuleEvaluation
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.permissions.matching import PathMatcher, compile_matcher

_logger = structlog.get_logger(__name__)

_RULES_QUERY = text(
    "SELECT tpr.effect, tpr.methods, tpr.path, tpr.operations, tpr.match_mode "
    "FROM toolkit_permission_rules tpr "
    "JOIN toolkit_credential_bindings tcb "
    "  ON tcb.toolkit_id = tpr.toolkit_id AND tcb.credential_id = tpr.credential_id "
    "JOIN credentials c ON c.id = tcb.credential_id "
    "WHERE tpr.toolkit_id = :toolkit_id "
    "  AND c.api_vendor = :api_vendor "
    "ORDER BY tpr.sequence ASC"
)

DEFAULT_RULE_CACHE_TTL_SECONDS = 30.0
DEFAULT_MAX_CACHE_ENTRIES = 5_000


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """A single permission rule — immutable value object for cache safety."""

    effect: str
    methods: frozenset[str] | None
    path: PathMatcher | None
    operations: tuple[str, ...] | None


def _compile_path_for_rule(raw: str | None, mode: str, *, toolkit_id: str) -> PathMatcher | None:
    """Compile a stored path pattern; log-once on a fail-closed row.

    Delegates to the shared seam and warns when a stored pattern was
    unparseable (a legacy row that predates save-time validation). The
    resulting matcher never matches — the opposite of the pre-#751 silent
    wildcard — and the warning identifies the misconfigured toolkit so an
    operator can fix the offending row.
    """
    matcher = compile_matcher(raw, mode)
    if matcher is not None and matcher.never:
        _logger.warning(
            "Ignoring toolkit permission rule with an invalid stored path pattern "
            "(fail-closed — the rule never matches); fix the pattern to restore intent",
            toolkit_id=toolkit_id,
            path=raw,
            match_mode=mode,
        )
    return matcher


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


def _normalize_methods(raw: list[str] | None) -> frozenset[str] | None:
    if raw is None:
        return None
    return frozenset(m.upper() for m in raw)


def _is_condition_less(rule: PermissionRule) -> bool:
    """True if a rule constrains nothing — matches every request when evaluated."""
    return rule.methods is None and rule.path is None and rule.operations is None


def _rule_matches(
    rule: PermissionRule, *, method: str, path: str, operation_id: str | None
) -> bool:
    """Return True if ALL defined criteria in the rule match the request."""
    if rule.methods is not None and method.upper() not in rule.methods:
        return False
    if rule.path is not None and not rule.path.matches(path):
        return False
    if rule.operations is not None:
        return operation_id is not None and operation_id in rule.operations
    return True


def evaluate_rules(
    rules: list[PermissionRule],
    *,
    method: str,
    path: str,
    operation_id: str | None,
    toolkit_id: str | None = None,
) -> bool:
    """Evaluate an ordered list of permission rules. Returns True if allowed.

    ``toolkit_id`` is optional and only used to make the condition-less-`allow`
    warning actionable — it identifies which binding is misconfigured.
    """
    for rule in rules:
        # Defense-in-depth: a condition-less `allow` is an unrestricted grant
        # (matches everything) and should have been rejected at the API schema.
        # If one reaches the broker it is a misconfiguration — skip it rather
        # than honour blanket access. A condition-less `deny` keeps its
        # legitimate match-all catch-all behaviour.
        if _is_condition_less(rule) and rule.effect.lower() == "allow":
            _logger.warning(
                "Ignoring misconfigured condition-less 'allow' permission rule "
                "(matches all requests); skipping to next rule",
                toolkit_id=toolkit_id,
            )
            continue
        if _rule_matches(rule, method=method, path=path, operation_id=operation_id):
            return rule.effect.lower() == "allow"
    return False


@dataclass(slots=True)
class _CacheEntry:
    """A cached rule list with its insertion time (monotonic)."""

    rules: list[PermissionRule]
    cached_at: float


class RuleEvaluator:
    """Evaluates toolkit permission rules with TTL-LRU caching.

    Implements ``RuleEvaluatorProtocol``. A cache hit within
    ``cache_ttl_seconds`` returns the cached rule list without touching the DB;
    concurrent misses for the same key are coalesced via single-flight.
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
        toolkit_id: str,
        method: str,
        path: str,
        operation_id: str | None,
        api_vendor: str = "",
    ) -> RuleEvaluation:
        """Evaluate permission rules for the toolkit.

        Returns a :class:`RuleEvaluation` — ``allowed`` plus the vendor-pooled
        rule count so the router can distinguish "no rules loaded for this
        vendor" (rules_loaded == 0) from "loaded but nothing matched" in the
        deny problem detail (#578).
        """
        rules = await self._get_rules(toolkit_id, api_vendor)
        if not rules:
            return RuleEvaluation(allowed=False, rules_loaded=0)
        allowed = evaluate_rules(
            rules,
            method=method,
            path=path,
            operation_id=operation_id,
            toolkit_id=toolkit_id,
        )
        return RuleEvaluation(allowed=allowed, rules_loaded=len(rules))

    async def _get_rules(self, toolkit_id: str, api_vendor: str) -> list[PermissionRule]:
        """Fetch rules from cache or DB (single-flighted)."""
        cache_key = f"{toolkit_id}:{api_vendor}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and (now - cached.cached_at) < self._cache_ttl_seconds:
            self._cache.move_to_end(cache_key)
            return cached.rules

        async def _load() -> list[PermissionRule]:
            rules = await self._fetch_rules(toolkit_id, api_vendor)
            self._store(cache_key, _CacheEntry(rules=rules, cached_at=time.monotonic()))
            return rules

        return await self._single_flight.do(cache_key, _load)

    async def _fetch_rules(self, toolkit_id: str, api_vendor: str) -> list[PermissionRule]:
        """Load rules from the control DB, scoped to matching credentials."""
        async with self._control_db.session() as session:
            rows = (
                await session.execute(
                    _RULES_QUERY, {"toolkit_id": toolkit_id, "api_vendor": api_vendor}
                )
            ).all()
        return [
            PermissionRule(
                effect=row[0],
                methods=_normalize_methods(_coerce_json_list(row[1])),
                path=_compile_path_for_rule(row[2], str(row[4] or "regex"), toolkit_id=toolkit_id),
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
