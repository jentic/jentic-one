"""Canonical operation-identity resolution for permission-rule evaluation.

Both live broker authorization and ``POST .../permissions:test`` dry-run resolve
operations to one identity contract before calling ``first_matching_rule``:

- ``operation_id`` carries the spec's OpenAPI ``operationId`` when resolved
  unambiguously within the credential's covered API scope.
- ``registry_operation_id`` carries the revision-scoped ``op_<40-hex>`` primary
  key so rules written with a registry operation id continue to match that
  revision's operation.
- ``ambiguous_operation_ids`` names OpenAPI ``operationId``s that collide across
  multiple live APIs or multiple dry-run candidates under the credential's
  covered scope; ``allow`` and ``require-approval`` rules naming them fail
  closed, while ``deny`` rules still deny.

This module is pure (no framework, ORM, or surface imports) so ``broker`` and
``control`` share one implementation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from jentic_one.shared.models.api_identity import CredentialScope, credential_covers
from jentic_one.shared.permissions.evaluation import is_registry_operation_id
from jentic_one.shared.url_path import normalize_path

_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
_RFC6570_OPERATORS = frozenset("+#./;?&")
_SAFE_GROUP_RE = re.compile(r"[^A-Za-z0-9_]")


@dataclass(frozen=True, slots=True)
class LiveOperationRow:
    """One operation belonging to an API's live (``current_revision_id``) revision."""

    api_vendor: str
    api_name: str
    api_version: str
    id: str
    operation_id: str | None
    method: str
    path: str


@dataclass(frozen=True, slots=True)
class ResolvedRuleOperation:
    """Resolved operation identity inputs for ``first_matching_rule``."""

    operation_id: str | None = None
    registry_operation_id: str | None = None
    ambiguous_operation_ids: frozenset[str] = frozenset()


def _split_param_token(token: str) -> tuple[str, bool]:
    if token and token[0] in _RFC6570_OPERATORS:
        return token[1:], token[0] == "+"
    return token, False


def _normalize_path_template(template: str) -> str:
    parts = _PATH_PARAM_RE.split(template)
    tokens: list[str] = []
    shielded: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 0:
            shielded.append(part)
        else:
            tokens.append(part)
            shielded.append(f"\x00{len(tokens) - 1}\x00")
    normalized = normalize_path("".join(shielded))
    for idx, token in enumerate(tokens):
        normalized = normalized.replace(f"\x00{idx}\x00", "{" + token + "}")
    return normalized


def _compile_spec_template(normalized_template: str) -> tuple[re.Pattern[str], bool]:
    parts = _PATH_PARAM_RE.split(normalized_template)
    raw_tokens = [parts[i] for i in range(1, len(parts), 2)]
    is_parameterized = bool(raw_tokens)

    catch_all_by_name: dict[str, bool] = {}
    group_by_name: dict[str, str] = {}
    taken_groups: set[str] = set()
    for token in raw_tokens:
        name, is_catch_all = _split_param_token(token)
        catch_all_by_name[name] = catch_all_by_name.get(name, True) and is_catch_all
        if name not in group_by_name:
            base = _SAFE_GROUP_RE.sub("_", name)
            if not base or base[0].isdigit():
                base = f"p_{base}"
            group = base
            suffix = 2
            while group in taken_groups:
                group = f"{base}__{suffix}"
                suffix += 1
            group_by_name[name] = group
            taken_groups.add(group)

    seen_groups: set[str] = set()
    regex_parts: list[str] = ["^"]
    for idx, part in enumerate(parts):
        if idx % 2 == 0:
            regex_parts.append(re.escape(part))
            continue
        name, _ = _split_param_token(part)
        body = ".+" if catch_all_by_name.get(name, False) else "[^/]+"
        group = group_by_name[name]
        if group in seen_groups:
            regex_parts.append(f"(?P={group})")
        else:
            seen_groups.add(group)
            regex_parts.append(f"(?P<{group}>{body})")
    regex_parts.append("$")
    return re.compile("".join(regex_parts)), is_parameterized


def _specificity_key(
    normalized_template: str, *, is_parameterized: bool
) -> tuple[bool, int, int, int]:
    template_parts = normalized_template.strip("/").split("/")
    literal_count = sum(1 for p in template_parts if not p.startswith("{"))
    first_literal_pos = next(
        (i for i, p in enumerate(template_parts) if not p.startswith("{")),
        len(template_parts),
    )
    last_literal_pos = next(
        (i for i, p in reversed(list(enumerate(template_parts))) if not p.startswith("{")),
        -1,
    )
    return (is_parameterized, -literal_count, first_literal_pos, -last_literal_pos)


def match_spec_path(
    template: str, normalized_request_path: str
) -> tuple[bool, tuple[bool, int, int, int]]:
    """Match a normalized server-relative request path against a spec path template.

    Mirrors ``URLLookupService._match_and_rank`` specificity ordering so dry-run
    resolution picks the exact operation live URL lookup selects within an API.
    """
    normalized_template = _normalize_path_template(template)
    pattern, is_parameterized = _compile_spec_template(normalized_template)
    if pattern.fullmatch(normalized_request_path) is None:
        return False, (True, 0, 0, 0)
    return True, _specificity_key(normalized_template, is_parameterized=is_parameterized)


def resolve_live_operation(
    *,
    spec_operation_id: str | None,
    registry_operation_id: str | None,
    colliding_operation_ids: frozenset[str] = frozenset(),
) -> ResolvedRuleOperation:
    """Build the canonical rule-evaluation operation identity for an execution.

    When a caller passes a non-``op_<40-hex>`` string in ``registry_operation_id``
    with ``spec_operation_id=None`` (e.g. synthetic unit-test fixtures), it is
    treated as the OpenAPI ``operationId`` rather than a registry primary key.
    """
    reg_id: str | None = None
    spec_id = spec_operation_id
    if registry_operation_id is not None:
        if is_registry_operation_id(registry_operation_id):
            reg_id = registry_operation_id
        elif spec_id is None:
            spec_id = registry_operation_id

    if spec_id is not None and spec_id in colliding_operation_ids:
        return ResolvedRuleOperation(
            operation_id=None,
            registry_operation_id=reg_id,
            ambiguous_operation_ids=frozenset({spec_id}),
        )
    return ResolvedRuleOperation(
        operation_id=spec_id,
        registry_operation_id=reg_id,
    )


def _best_for_api(
    api_rows: Sequence[LiveOperationRow], *, method: str, normalized_path: str
) -> tuple[list[LiveOperationRow], bool]:
    """Return ``(winning_rows, tied_within_api)`` for one API's live operations."""
    methods_to_try = [method.upper()]
    if method.upper() == "HEAD":
        methods_to_try.append("GET")

    for target_method in methods_to_try:
        ranked: list[tuple[tuple[bool, int, int, int], LiveOperationRow]] = []
        for row in api_rows:
            if row.method.upper() != target_method:
                continue
            matched, key = match_spec_path(row.path, normalized_path)
            if matched:
                ranked.append((key, row))
        if not ranked:
            continue
        ranked.sort(key=lambda item: item[0])
        best_key = ranked[0][0]
        winners = [row for key, row in ranked if key == best_key]
        return winners, len(winners) > 1
    return [], False


def resolve_dry_run_operation(
    rows: Sequence[LiveOperationRow],
    *,
    scope: CredentialScope,
    method: str,
    normalized_path: str,
    requested_operation_id: str | None,
) -> ResolvedRuleOperation:
    """Resolve ``(method, path, operation_id)`` against live operations covered by ``scope``.

    Enforces parity with live broker discovery and authorization:
    - Only APIs covered by ``scope`` participate; unrelated vendors or APIs
      outside ``scope`` never contribute candidates or ambiguity.
    - Any OpenAPI ``operationId`` declared by more than one covered live API is
      marked ambiguous and fails closed.
    - When ``requested_operation_id`` is supplied, it must agree with a matched
      ``(method, path)`` candidate (by ``Operation.id`` for ``op_<40-hex>``, or
      by ``Operation.operation_id`` otherwise); a mismatched or superseded id
      resolves no operation.
    - When ``requested_operation_id`` is omitted and multiple covered APIs match
      ``(method, path)``, all candidate ``operationId``s are marked ambiguous so
      operation-scoped ``allow``/``require-approval`` rules fail closed while
      method/path-only rules continue to evaluate normally.
    """
    covered_by_api: dict[tuple[str, str, str], list[LiveOperationRow]] = defaultdict(list)
    apis_by_spec_id: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in rows:
        if not credential_covers(
            scope, vendor=row.api_vendor, name=row.api_name, version=row.api_version
        ):
            continue
        api_key = (row.api_vendor, row.api_name, row.api_version)
        covered_by_api[api_key].append(row)
        if row.operation_id is not None:
            apis_by_spec_id[row.operation_id].add(api_key)

    colliding_spec_ids = frozenset(
        spec_id for spec_id, apis in apis_by_spec_id.items() if len(apis) > 1
    )

    candidates: list[LiveOperationRow] = []
    tied_spec_ids: set[str] = set()
    for api_key in sorted(covered_by_api):
        winners, tied = _best_for_api(
            covered_by_api[api_key], method=method, normalized_path=normalized_path
        )
        if tied:
            for row in winners:
                if row.operation_id is not None:
                    tied_spec_ids.add(row.operation_id)
            continue
        candidates.extend(winners)

    if requested_operation_id is not None:
        if is_registry_operation_id(requested_operation_id):
            candidates = [c for c in candidates if c.id == requested_operation_id]
        else:
            candidates = [c for c in candidates if c.operation_id == requested_operation_id]

    if not candidates:
        if tied_spec_ids:
            return ResolvedRuleOperation(ambiguous_operation_ids=frozenset(tied_spec_ids))
        return ResolvedRuleOperation()

    if len(candidates) > 1:
        ambiguous = (
            frozenset(c.operation_id for c in candidates if c.operation_id is not None)
            | colliding_spec_ids
            | frozenset(tied_spec_ids)
        )
        return ResolvedRuleOperation(ambiguous_operation_ids=ambiguous)

    winner = candidates[0]
    ambiguous_all = colliding_spec_ids | frozenset(tied_spec_ids)
    if winner.operation_id is not None and winner.operation_id in ambiguous_all:
        return ResolvedRuleOperation(
            operation_id=None,
            registry_operation_id=winner.id,
            ambiguous_operation_ids=frozenset({winner.operation_id}),
        )
    return ResolvedRuleOperation(
        operation_id=winner.operation_id,
        registry_operation_id=winner.id,
    )


def colliding_spec_operation_ids(
    api_identities: Sequence[tuple[str, str]],
    *,
    scope: CredentialScope,
    spec_operation_id: str,
) -> frozenset[str]:
    """Return ``frozenset({spec_operation_id})`` when >1 covered live API declares it."""
    covered_apis = {
        (name, version)
        for name, version in api_identities
        if credential_covers(scope, vendor=scope.vendor, name=name, version=version)
    }
    if len(covered_apis) > 1:
        return frozenset({spec_operation_id})
    return frozenset()


__all__ = [
    "LiveOperationRow",
    "ResolvedRuleOperation",
    "colliding_spec_operation_ids",
    "match_spec_path",
    "resolve_dry_run_operation",
    "resolve_live_operation",
]
