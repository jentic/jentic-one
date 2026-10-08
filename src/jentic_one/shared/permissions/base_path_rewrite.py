"""Rewrite binding rules authored against the full upstream path (#1424).

Binding permission rules are enforced on the spec-relative request path (see
``shared.permissions.evaluation``). A ``prefix``/``exact`` rule written with
the API's server base path baked in (``/eu/widgets`` for a server
``http://host/{region}``, ``/api/v3/pet`` for Petstore) matches nothing on
that basis. This module decides — purely, no I/O — whether such a rule can be
rewritten to its spec-relative form, and refuses whenever the answer is not
unambiguous:

* ``regex`` rules are never rewritten (no mechanical transform is sound).
* Only a **static** base path (``/api/v3``) is stripped automatically. A base
  with a server variable (``/{region}``) is reported instead: ``/eu/widgets``
  allowed only the ``eu`` region, and its spec-relative form ``/widgets``
  would allow every region — rules cannot express a server-variable value,
  so narrowing has to move to the credential's server-variable scoping.
* A rule whose path already applies to one of the API's operations is left
  alone — unless it *also* reads as base-qualified under a concrete base
  (static or enum), in which case it is reported as ambiguous.
* A rewrite is only proposed when stripping a server base yields a path that
  applies to at least one operation; two bases yielding different paths, or
  APIs (for a shared rule set) that disagree, are reported instead.

"Applies to an operation" is template-aware, mirroring the UI preview's
matcher (``ui/src/shared/credentials/lib/template-matcher.ts``): an ``exact``
path must be a concrete instance of the operation template, a ``prefix`` must
be an opening slice of one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

_PLACEHOLDER_SEGMENT_RE = re.compile(r"^\{[^}]+\}$")
_TEMPLATE_TOKEN_RE = re.compile(r"(\{[^}]+\})")


class BasePathKind(StrEnum):
    #: No server variable in the base path (``/api/v3``).
    STATIC = "static"
    #: A server variable with a declared enum (``/{region}`` ∈ {eu, us}).
    ENUM = "enum"
    #: A free-form server variable (any single segment).
    FREEFORM = "freeform"


@dataclass(frozen=True, slots=True)
class BasePath:
    """One server's base path: a regex source full-matching it, and its kind.

    A server variable becomes its enum alternation (``ENUM``) or a
    single-segment wildcard (``FREEFORM``).
    """

    pattern: str
    kind: BasePathKind


@dataclass(frozen=True, slots=True)
class ApiPathShape:
    """What a rewrite needs to know about one API's live revision.

    ``base_paths`` has one entry per distinct server base (servers with no
    base path contribute none). ``operation_templates`` are the spec's
    (server-relative) operation path templates.
    """

    base_paths: tuple[BasePath, ...]
    operation_templates: tuple[str, ...]


class ApiPathShapeReaderProtocol(Protocol):
    """Reads an API's :class:`ApiPathShape` (implemented over the registry DB)."""

    async def get(self, *, vendor: str, name: str, version: str) -> ApiPathShape | None:
        """The live revision's shape, or ``None`` if the API has no live revision."""
        ...


class RewriteOutcome(StrEnum):
    REWRITE = "rewrite"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"


class SkipReason(StrEnum):
    REGEX_NOT_REWRITABLE = "regex_not_rewritable"
    API_NOT_FOUND = "api_not_found"
    AMBIGUOUS_BASE = "ambiguous_base"
    MATCHES_NO_OPERATION = "matches_no_operation"
    RULE_SET_MIXED_APIS = "rule_set_mixed_apis"
    SERVER_VARIABLE_BASE = "server_variable_base"
    SPEC_PATH_OR_BASE_QUALIFIED = "spec_path_or_base_qualified"
    # Decided by the service before the shapes are consulted:
    CREDENTIAL_NOT_FOUND = "credential_not_found"
    CREDENTIAL_NOT_API_SCOPED = "credential_not_api_scoped"
    BINDING_NOT_FOUND = "binding_not_found"
    RULE_SET_NOT_ATTACHED = "rule_set_not_attached"


@dataclass(frozen=True, slots=True)
class RewriteDecision:
    outcome: RewriteOutcome
    new_path: str | None = None
    reason: SkipReason | None = None

    @staticmethod
    def unchanged() -> RewriteDecision:
        return RewriteDecision(RewriteOutcome.UNCHANGED)

    @staticmethod
    def skipped(reason: SkipReason) -> RewriteDecision:
        return RewriteDecision(RewriteOutcome.SKIPPED, reason=reason)


def _template_regex(template: str) -> re.Pattern[str]:
    parts = _TEMPLATE_TOKEN_RE.split(normalize_template(template))
    body = "".join("[^/]+" if i % 2 else re.escape(p) for i, p in enumerate(parts))
    return re.compile(body)


def normalize_template(template: str) -> str:
    """Canonical trailing-slash form of a template (``/a/`` → ``/a``)."""
    return template.rstrip("/") or "/"


def _prefix_can_apply(prefix: str, template: str) -> bool:
    p_parts = prefix.split("/")
    t_parts = normalize_template(template).split("/")
    if len(p_parts) > len(t_parts):
        return False
    for i, p_seg in enumerate(p_parts):
        t_seg = t_parts[i]
        if _PLACEHOLDER_SEGMENT_RE.match(t_seg):
            continue
        if i == len(p_parts) - 1:
            if not t_seg.startswith(p_seg):
                return False
        elif t_seg != p_seg:
            return False
    return True


def path_applies_to_template(path: str, mode: str, template: str) -> bool:
    """Could a ``prefix``/``exact`` rule path match some instance of ``template``?"""
    if mode == "exact":
        return _template_regex(template).fullmatch(path) is not None
    if mode == "prefix":
        return _prefix_can_apply(path, template)
    raise ValueError(f"unsupported match mode for template check: {mode!r}")


def _applies(path: str, mode: str, shape: ApiPathShape) -> bool:
    return any(path_applies_to_template(path, mode, t) for t in shape.operation_templates)


def _decide_for_shape(path: str, mode: str, shape: ApiPathShape) -> RewriteDecision:
    # Every (stripped path, base kind) a server base yields for this rule.
    stripped: list[tuple[str, BasePathKind]] = []
    for base in shape.base_paths:
        match = re.match(f"(?:{base.pattern})(?=/|$)", path)
        if match is not None:
            stripped.append((path[match.end() :] or "/", base.kind))
    applicable = [(c, kind) for c, kind in stripped if _applies(c, mode, shape)]

    if _applies(path, mode, shape):
        # Already fits an operation. A free-form base (``/{tenant}``) strips
        # any first segment, so it proves nothing; a concrete base that also
        # yields a fitting path means the rule might be base-qualified.
        if any(kind is not BasePathKind.FREEFORM for _, kind in applicable):
            return RewriteDecision.skipped(SkipReason.SPEC_PATH_OR_BASE_QUALIFIED)
        return RewriteDecision.unchanged()
    if not stripped:
        return RewriteDecision.unchanged()
    if not applicable:
        return RewriteDecision.skipped(SkipReason.MATCHES_NO_OPERATION)
    if any(kind is not BasePathKind.STATIC for _, kind in applicable):
        return RewriteDecision.skipped(SkipReason.SERVER_VARIABLE_BASE)
    candidates = {c for c, _ in applicable}
    if len(candidates) > 1:
        return RewriteDecision.skipped(SkipReason.AMBIGUOUS_BASE)
    return RewriteDecision(RewriteOutcome.REWRITE, new_path=candidates.pop())


def decide_rewrite(
    path: str | None, mode: str, shapes: Sequence[ApiPathShape | None]
) -> RewriteDecision:
    """Decide whether one rule's ``path`` should be rewritten.

    ``shapes`` are the shapes of every API the rule applies to — one for an
    inline binding rule, one per attached binding for a shared rule set. A
    ``None`` entry is an API with no live revision.
    """
    if path is None:
        return RewriteDecision.unchanged()
    if mode not in ("prefix", "exact"):
        return RewriteDecision.skipped(SkipReason.REGEX_NOT_REWRITABLE)
    if not shapes:
        return RewriteDecision.unchanged()
    if any(shape is None for shape in shapes):
        return RewriteDecision.skipped(SkipReason.API_NOT_FOUND)
    decisions = {_decide_for_shape(path, mode, shape) for shape in shapes if shape is not None}
    if len(decisions) > 1:
        return RewriteDecision.skipped(SkipReason.RULE_SET_MIXED_APIS)
    return decisions.pop()
