"""Toolkit permission-rule evaluator — enforces row-level ALLOW/DENY rules.

Queries ``toolkit_permission_rules`` from the control DB (raw SQL — the broker
cannot import control ORM) and evaluates the ordered rule list against the
inbound request. A first-match-wins policy terminates on the first matching
rule's effect; an exhausted rule list defaults to DENY (secure-by-default).

Performance: the rule list per (toolkit, credential) pair is short-TTL cached
(LRU + single-flight) so the hot-path DB hit is amortised across requests.
"""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog
from sqlalchemy import text

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.db import DatabaseSession

_logger = structlog.get_logger(__name__)

_MAX_PATTERN_LENGTH = 1000

_RULES_QUERY = text(
    "SELECT tpr.effect, tpr.methods, tpr.path, tpr.operations "
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
    path: re.Pattern[str] | None
    operations: tuple[str, ...] | None


def _compile_path(raw: str | None) -> re.Pattern[str] | None:
    """Pre-compile a path pattern, returning None for invalid/oversized patterns."""
    if raw is None:
        return None
    if len(raw) > _MAX_PATTERN_LENGTH:
        return None
    try:
        return re.compile(raw)
    except re.error:
        return None


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
    if rule.path is not None and not rule.path.match(path):
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
    ) -> bool:
        """Evaluate permission rules for the toolkit. Returns True if allowed."""
        rules = await self._get_rules(toolkit_id, api_vendor)
        if not rules:
            return False
        return evaluate_rules(
            rules,
            method=method,
            path=path,
            operation_id=operation_id,
            toolkit_id=toolkit_id,
        )

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
                path=_compile_path(row[2]),
                operations=(
                    tuple(ops) if (ops := _coerce_json_list(row[3])) is not None else None
                ),
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
