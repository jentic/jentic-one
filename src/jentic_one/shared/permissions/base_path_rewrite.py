"""Rewrite binding rules authored against the full upstream path (#1424).

Binding permission rules are enforced on the spec-relative request path (see
``shared.permissions.evaluation``). A ``prefix``/``exact`` rule written with
the API's server base path baked in (``/eu/widgets`` for a server
``http://host/{region}``, ``/api/v3/pet`` for Petstore) matches nothing on
that basis. This module decides — purely, no I/O — whether such a rule can be
rewritten to its spec-relative form, and refuses whenever the answer is not
unambiguous:

* ``regex`` rules are never rewritten (no mechanical transform is sound).
* A rule whose path already applies to one of the API's operations is left
  alone — it is spec-relative already.
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


@dataclass(frozen=True, slots=True)
class ApiPathShape:
    """What a rewrite needs to know about one API's live revision.

    ``base_path_patterns`` are regex sources, one per distinct server URL,
    that full-match that server's base path (a templated segment becomes its
    enum alternation or a single-segment wildcard). Servers with no base
    path contribute none. ``operation_templates`` are the spec's
    (server-relative) operation path templates.
    """

    base_path_patterns: tuple[str, ...]
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


def _decide_for_shape(path: str, mode: str, shape: ApiPathShape) -> RewriteDecision:
    if any(path_applies_to_template(path, mode, t) for t in shape.operation_templates):
        return RewriteDecision.unchanged()
    stripped: set[str] = set()
    for pattern in shape.base_path_patterns:
        match = re.match(f"(?:{pattern})(?=/|$)", path)
        if match is None:
            continue
        stripped.add(path[match.end() :] or "/")
    if not stripped:
        return RewriteDecision.unchanged()
    applicable = {
        candidate
        for candidate in stripped
        if any(path_applies_to_template(candidate, mode, t) for t in shape.operation_templates)
    }
    if not applicable:
        return RewriteDecision.skipped(SkipReason.MATCHES_NO_OPERATION)
    if len(applicable) > 1:
        return RewriteDecision.skipped(SkipReason.AMBIGUOUS_BASE)
    return RewriteDecision(RewriteOutcome.REWRITE, new_path=applicable.pop())


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
