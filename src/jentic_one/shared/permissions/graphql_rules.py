"""GraphQL top-level-field rules: the selector a rule stores, and how verdicts combine.

A permission rule with ``match_mode="graphql"`` matches GraphQL requests by
operation type and top-level field name instead of by path. Its ``path``
column holds the selector:

- ``query`` or ``mutation``: every top-level field of that operation type;
- ``mutation:createPullRequest,addComment``: only the listed fields.

Field names follow the GraphQL ``Name`` grammar and are matched exactly (they
are case-sensitive in GraphQL). A rule's ``methods`` and ``operations`` still
apply on top of the selector.

The selector lives in ``path`` with its own ``match_mode`` so that a reader
that does not know the mode fails closed: every rule evaluator compiles
``(path, match_mode)`` through
:func:`~jentic_one.shared.permissions.matching.compile_matcher`, and an evaluator
without GraphQL rules sees an unknown mode with a non-empty ``path``, which
compiles to a matcher that never matches. An ``allow`` or
``require-approval`` GraphQL rule therefore grants nothing there. (A separate
column would be ignored by such a reader, leaving the rule's methods and path
to allow every GraphQL call.)

A GraphQL request is evaluated once per top-level field, each through the
ordinary first-match-wins rule list; the request's verdict is the most
restrictive of its fields' (:func:`combine_field_verdicts`), so one denied
field denies the whole document.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from jentic_one.shared.permissions.graphql_document import (
    GRAPHQL_OPERATION_TYPES,
    GraphQLOperationType,
)

#: The ``match_mode`` of a GraphQL top-level-field rule.
GRAPHQL_MATCH_MODE = "graphql"

#: Cap on the stored selector, matching the ``VARCHAR(1000)`` ``path`` column.
MAX_SELECTOR_LENGTH = 1000

_NAME_RE = re.compile(r"[_A-Za-z][_0-9A-Za-z]*")

# Verdict severity, least to most restrictive.
_SEVERITY = {"allow": 0, "require-approval": 1, "deny": 2}


@dataclass(frozen=True, slots=True)
class GraphQLSelector:
    """A compiled GraphQL rule selector; ``fields`` ``None`` means every field."""

    operation_type: GraphQLOperationType
    fields: frozenset[str] | None = None

    def matches(self, field: GraphQLField) -> bool:
        """Whether this selector covers one top-level field of a request."""
        if field.operation_type != self.operation_type:
            return False
        return self.fields is None or field.name in self.fields


@dataclass(frozen=True, slots=True)
class GraphQLField:
    """One top-level field of a GraphQL request, as rules are evaluated on it."""

    operation_type: GraphQLOperationType
    name: str


def selector_error(raw: str | None) -> str | None:
    """Why ``raw`` is not a valid selector, or ``None`` when it is."""
    if raw is None or raw == "":
        return (
            "a 'graphql' rule needs a selector in 'path': 'query', 'mutation', "
            "or '<type>:<field>,<field>'"
        )
    if len(raw) > MAX_SELECTOR_LENGTH:
        return f"path exceeds maximum length ({MAX_SELECTOR_LENGTH} characters)"
    operation_type, separator, field_list = raw.partition(":")
    if operation_type not in GRAPHQL_OPERATION_TYPES:
        return (
            f"unknown GraphQL operation type {operation_type!r}; expected one of "
            f"{list(GRAPHQL_OPERATION_TYPES)}"
        )
    if not separator:
        return None
    names = field_list.split(",")
    if not field_list or any(_NAME_RE.fullmatch(name) is None for name in names):
        return (
            "the field list must be comma-separated GraphQL field names with no spaces, "
            "e.g. 'mutation:createPullRequest,addComment'"
        )
    return None


def parse_selector(raw: str | None) -> GraphQLSelector | None:
    """Compile a stored selector; ``None`` when it is invalid (the caller fails closed)."""
    if raw is None or selector_error(raw) is not None:
        return None
    operation_type, separator, field_list = raw.partition(":")
    fields = frozenset(field_list.split(",")) if separator else None
    return GraphQLSelector(
        operation_type=operation_type,  # type: ignore[arg-type]  # checked by selector_error
        fields=fields,
    )


@dataclass(frozen=True, slots=True)
class FieldVerdict:
    """One top-level field's verdict and the rule that decided it (``None``: no rule)."""

    field: str
    verdict: str
    rule_index: int | None = None


def combine_field_verdicts(verdicts: Sequence[FieldVerdict]) -> FieldVerdict | None:
    """The most restrictive field verdict (``deny`` over ``require-approval`` over ``allow``).

    The first field in document order wins a tie, so the deciding field is
    stable. ``None`` for an empty sequence.
    """
    deciding: FieldVerdict | None = None
    for verdict in verdicts:
        if deciding is None or _SEVERITY.get(verdict.verdict, 2) > _SEVERITY.get(
            deciding.verdict, 2
        ):
            deciding = verdict
    return deciding


__all__ = [
    "GRAPHQL_MATCH_MODE",
    "MAX_SELECTOR_LENGTH",
    "FieldVerdict",
    "GraphQLField",
    "GraphQLSelector",
    "combine_field_verdicts",
    "parse_selector",
    "selector_error",
]
