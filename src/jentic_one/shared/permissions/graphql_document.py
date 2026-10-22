"""GraphQL request inspection: the operation type and top-level fields of one request.

Permission rules on a GraphQL endpoint match on what the document does, never on
the client-chosen operation name: one ``POST /graphql`` can carry any query or
mutation. :func:`inspect_graphql_request` reads a GraphQL-over-HTTP request and
returns its operation type and the **field names** (never aliases) selected on
the root type, or the reason it is refused.

The document is parsed by ``graphql-core``, a full implementation of the
GraphQL specification's grammar, so the broker and the upstream read the same
document the same way; no part of it is parsed by hand.

Top-level fields are collected after expanding, at the root level only, inline
fragments (``... on Mutation { … }`` or ``... { … }``) and fragment spreads
(``...F`` with ``fragment F on Mutation``), recursively. Directives such as
``@skip`` are not evaluated: a conditionally skipped field still counts, so the
field list can only be longer than what the upstream runs, never shorter.

A request is refused when it is anything but one JSON object carrying exactly
one ``query`` or ``mutation`` operation over ``POST``:

- a method other than ``POST`` (GraphQL over ``GET`` carries the document in
  the URL), or a URL query string;
- a body that is not UTF-8 JSON, has a duplicate key at any depth, is a JSON
  array (a batch), or has a key other than ``query``, ``variables`` and
  ``operationName`` (``extensions.persistedQuery`` and other extensions
  included), or no ``query`` string;
- a document that does not parse, has more than one operation (whatever
  ``operationName`` says), has a type-system definition, a duplicate fragment
  name, an unknown or cyclic fragment spread, or a ``subscription``;
- a root-level fragment on a type other than the operation's root type
  (``Query`` or ``Mutation``);
- an ``operationName`` that does not name the one operation.

The root meta fields ``__typename``, ``__schema`` and ``__type`` are ordinary
top-level fields here: they are returned like any other and rules match them
by name.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from graphql import GraphQLError, parse
from graphql.language import (
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    OperationDefinitionNode,
    OperationType,
    SelectionSetNode,
)

GraphQLOperationType = Literal["query", "mutation"]
"""The operation types a GraphQL request may carry (subscriptions are refused)."""

GRAPHQL_OPERATION_TYPES: tuple[GraphQLOperationType, ...] = ("query", "mutation")

#: The root type each operation type selects on. GitHub's schema uses the
#: specification's default names; a root-level fragment on any other type is
#: refused rather than guessed at.
ROOT_TYPE_NAMES: Mapping[GraphQLOperationType, str] = {"query": "Query", "mutation": "Mutation"}

#: The body keys a GraphQL-over-HTTP request may carry.
_ALLOWED_BODY_KEYS = frozenset({"query", "variables", "operationName"})

#: Parser token budget. A real ``gh`` query is a few thousand tokens at most;
#: the cap bounds the parser's work on a hostile document before the body cap
#: (bytes) would.
MAX_DOCUMENT_TOKENS = 20_000


@dataclass(frozen=True, slots=True)
class GraphQLCall:
    """What one GraphQL request does: its operation type and top-level field names.

    ``top_level_fields`` are the field names on the root type, deduplicated in
    document order, after fragment expansion; never aliases.
    """

    operation_type: GraphQLOperationType
    top_level_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphQLRejection:
    """Why a GraphQL request is refused (a sentence for the caller)."""

    reason: str


class _RejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _no_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise _RejectedError(
                "the JSON body has a duplicate key; send each key once, because "
                "parsers disagree on which duplicate wins"
            )
        seen[key] = value
    return seen


def _decode_body(body: bytes | None) -> dict[str, Any]:
    if not body:
        raise _RejectedError("the request has no body; send the document as a JSON object")
    try:
        text = body.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except _RejectedError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _RejectedError("the body is not a UTF-8 JSON document") from None
    if isinstance(payload, list):
        raise _RejectedError("batched requests (a JSON array) are not supported; send one request")
    if not isinstance(payload, dict):
        raise _RejectedError("the body must be a JSON object")
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and "persistedQuery" in extensions:
        raise _RejectedError(
            "persisted queries are not supported; send the full document in 'query'"
        )
    unknown = sorted(set(payload) - _ALLOWED_BODY_KEYS)
    if unknown:
        raise _RejectedError(
            f"the body has unsupported key(s) {', '.join(repr(k) for k in unknown)}; "
            "only 'query', 'variables' and 'operationName' are accepted"
        )
    return payload


def _document(payload: Mapping[str, Any]) -> DocumentNode:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _RejectedError("the body has no 'query' string")
    variables = payload.get("variables")
    if variables is not None and not isinstance(variables, dict):
        raise _RejectedError("'variables' must be a JSON object")
    operation_name = payload.get("operationName")
    if operation_name is not None and not isinstance(operation_name, str):
        raise _RejectedError("'operationName' must be a string")
    try:
        return parse(query, no_location=True, max_tokens=MAX_DOCUMENT_TOKENS)
    except (GraphQLError, RecursionError):
        # The parser's message can quote the document, which may carry user
        # data; the reason stays generic.
        raise _RejectedError("the 'query' is not a valid GraphQL document") from None


def _operation_and_fragments(
    document: DocumentNode, operation_name: str | None
) -> tuple[OperationDefinitionNode, dict[str, FragmentDefinitionNode]]:
    operations: list[OperationDefinitionNode] = []
    fragments: dict[str, FragmentDefinitionNode] = {}
    for definition in document.definitions:
        if isinstance(definition, OperationDefinitionNode):
            operations.append(definition)
        elif isinstance(definition, FragmentDefinitionNode):
            name = definition.name.value
            if name in fragments:
                raise _RejectedError(f"the document defines fragment {name!r} more than once")
            fragments[name] = definition
        else:
            raise _RejectedError(
                "the document has a type-system definition; send an executable one"
            )
    if len(operations) != 1:
        raise _RejectedError(
            f"the document has {len(operations)} operations; send exactly one per request"
        )
    (operation,) = operations
    own_name = operation.name.value if operation.name is not None else None
    if operation_name is not None and operation_name != own_name:
        raise _RejectedError("'operationName' does not name the document's operation")
    return operation, fragments


class _RootFieldCollector:
    """Collects the root-level field names of one operation, expanding fragments.

    Each fragment's fields are collected once and reused, so a document whose
    fragments spread each other many times costs one pass per fragment, not
    one per spread.
    """

    def __init__(self, root_type: str, fragments: Mapping[str, FragmentDefinitionNode]) -> None:
        self._root_type = root_type
        self._fragments = fragments
        self._done: dict[str, tuple[str, ...]] = {}
        self._expanding: set[str] = set()

    def fields(self, selection_set: SelectionSetNode) -> tuple[str, ...]:
        """The field names ``selection_set`` selects on the root type, deduplicated in order."""
        names: list[str] = []
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                names.append(selection.name.value)
            elif isinstance(selection, InlineFragmentNode):
                condition = selection.type_condition
                if condition is not None and condition.name.value != self._root_type:
                    raise _RejectedError(
                        f"a root-level fragment is on type {condition.name.value!r}, "
                        f"not the root type {self._root_type!r}"
                    )
                names.extend(self.fields(selection.selection_set))
            elif isinstance(selection, FragmentSpreadNode):
                names.extend(self._fragment_fields(selection.name.value))
        return tuple(dict.fromkeys(names))

    def _fragment_fields(self, name: str) -> tuple[str, ...]:
        done = self._done.get(name)
        if done is not None:
            return done
        fragment = self._fragments.get(name)
        if fragment is None:
            raise _RejectedError(f"the document spreads undefined fragment {name!r}")
        if name in self._expanding:
            raise _RejectedError(f"fragment {name!r} spreads itself")
        if fragment.type_condition.name.value != self._root_type:
            raise _RejectedError(
                f"root-level fragment {name!r} is on type "
                f"{fragment.type_condition.name.value!r}, not the root type {self._root_type!r}"
            )
        self._expanding.add(name)
        fields = self.fields(fragment.selection_set)
        self._expanding.discard(name)
        self._done[name] = fields
        return fields


def _inspect(method: str, query_string: str, body: bytes | None) -> GraphQLCall:
    if method.upper() != "POST":
        raise _RejectedError(
            f"GraphQL over {method.upper()} is not supported; send the document with POST"
        )
    if query_string:
        raise _RejectedError(
            "the GraphQL endpoint takes no URL query string; send everything in the body"
        )
    payload = _decode_body(body)
    document = _document(payload)
    operation, fragments = _operation_and_fragments(document, payload.get("operationName"))
    if operation.operation is OperationType.QUERY:
        operation_type: GraphQLOperationType = "query"
    elif operation.operation is OperationType.MUTATION:
        operation_type = "mutation"
    else:
        raise _RejectedError("subscriptions are not supported")
    collector = _RootFieldCollector(ROOT_TYPE_NAMES[operation_type], fragments)
    return GraphQLCall(
        operation_type=operation_type,
        top_level_fields=collector.fields(operation.selection_set),
    )


def inspect_graphql_request(
    *, method: str, query_string: str, body: bytes | None
) -> GraphQLCall | GraphQLRejection:
    """The operation type and top-level field names of one GraphQL request, or why it is refused.

    ``query_string`` is the request URL's raw query (empty when there is
    none) and ``body`` the raw request body. Never raises for any input.
    """
    try:
        return _inspect(method, query_string, body)
    except _RejectedError as exc:
        return GraphQLRejection(reason=exc.reason)
    except RecursionError:
        return GraphQLRejection(reason="the GraphQL document is nested too deeply")


__all__ = [
    "GRAPHQL_OPERATION_TYPES",
    "MAX_DOCUMENT_TOKENS",
    "ROOT_TYPE_NAMES",
    "GraphQLCall",
    "GraphQLOperationType",
    "GraphQLRejection",
    "inspect_graphql_request",
]
