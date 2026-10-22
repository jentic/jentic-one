"""GraphQL request inspection: top-level field names, fragments, and every refused shape."""

from __future__ import annotations

import json
from typing import Any

import pytest

from jentic_one.shared.permissions.graphql_document import (
    MAX_DOCUMENT_TOKENS,
    GraphQLCall,
    GraphQLRejection,
    inspect_graphql_request,
)


def _body(query: str, **extra: Any) -> bytes:
    return json.dumps({"query": query, **extra}).encode()


def _inspect(body: bytes | None, *, method: str = "POST", query_string: str = "") -> Any:
    return inspect_graphql_request(method=method, query_string=query_string, body=body)


def _call(query: str, **extra: Any) -> GraphQLCall:
    result = _inspect(_body(query, **extra))
    assert isinstance(result, GraphQLCall), result
    return result


def _rejection(body: bytes | None, **kwargs: Any) -> str:
    result = _inspect(body, **kwargs)
    assert isinstance(result, GraphQLRejection), result
    return result.reason


# ── what a request does ──────────────────────────────────────────────────────


def test_query_fields_are_listed_in_document_order() -> None:
    call = _call('query { viewer { login } repository(owner: "o", name: "r") { id } }')

    assert call == GraphQLCall(operation_type="query", top_level_fields=("viewer", "repository"))


def test_shorthand_query_is_a_query() -> None:
    assert _call("{ viewer { login } }").operation_type == "query"


def test_mutation_fields_are_listed() -> None:
    call = _call("mutation M($i: CreatePullRequestInput!) { createPullRequest(input: $i) { a } }")

    assert call == GraphQLCall(operation_type="mutation", top_level_fields=("createPullRequest",))


def test_alias_is_resolved_to_the_field_name() -> None:
    call = _call('mutation { createPullRequest: deleteRef(input: {refId: "x"}) { a } }')

    assert call.top_level_fields == ("deleteRef",)


def test_repeated_field_is_listed_once() -> None:
    call = _call("mutation { a: addComment(input: {}) { b } c: addComment(input: {}) { d } }")

    assert call.top_level_fields == ("addComment",)


def test_inline_fragment_on_the_root_type_is_expanded() -> None:
    call = _call("mutation { ... on Mutation { deleteRef(input: {}) { a } } addComment { b } }")

    assert call.top_level_fields == ("deleteRef", "addComment")


def test_inline_fragment_without_type_condition_is_expanded() -> None:
    call = _call("mutation { ... @include(if: true) { mergeBranch(input: {}) { a } } }")

    assert call.top_level_fields == ("mergeBranch",)


def test_fragment_spread_on_the_root_type_is_expanded() -> None:
    call = _call(
        "mutation { ...Sneaky } fragment Sneaky on Mutation { x: updateRefs(input: {}) { a } }"
    )

    assert call.top_level_fields == ("updateRefs",)


def test_nested_fragments_are_expanded_recursively() -> None:
    call = _call(
        "mutation { ...A } "
        "fragment A on Mutation { addComment { a } ... on Mutation { ...B } } "
        "fragment B on Mutation { createCommitOnBranch(input: {}) { a } }"
    )

    assert call.top_level_fields == ("addComment", "createCommitOnBranch")


def test_fragments_below_the_root_are_not_expanded() -> None:
    call = _call("query { viewer { ...U } } fragment U on User { repositories { totalCount } }")

    assert call.top_level_fields == ("viewer",)


def test_skipped_field_still_counts() -> None:
    call = _call("mutation { deleteRef(input: {}) @skip(if: true) { a } }")

    assert call.top_level_fields == ("deleteRef",)


@pytest.mark.parametrize("field", ["__typename", "__schema", "__type"])
def test_introspection_fields_are_allowed_in_queries(field: str) -> None:
    selection = f'{field}(name: "Repository") {{ name }}' if field == "__type" else field
    if field == "__schema":
        selection = "__schema { queryType { name } }"

    call = _call(f"query {{ {selection} }}")

    assert call == GraphQLCall(operation_type="query", top_level_fields=(field,))


def test_variables_and_matching_operation_name_are_accepted() -> None:
    call = _call(
        "query PullRequests($n: Int) { viewer { login } }",
        variables={"n": 1},
        operationName="PullRequests",
    )

    assert call.top_level_fields == ("viewer",)


def test_null_variables_and_operation_name_are_accepted() -> None:
    assert _call("{ viewer { login } }", variables=None, operationName=None)


# ── refused shapes ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_any_method_but_post_is_refused(method: str) -> None:
    reason = _rejection(_body("{ viewer { login } }"), method=method)

    assert method in reason


def test_get_with_the_document_in_the_url_is_refused() -> None:
    reason = _rejection(None, method="GET", query_string="query=%7Bviewer%7Blogin%7D%7D")

    assert "GET" in reason


def test_url_query_string_on_post_is_refused() -> None:
    reason = _rejection(_body("{ viewer { login } }"), query_string="query=mutation")

    assert "query string" in reason


def test_more_than_one_operation_is_refused_whatever_operation_name_says() -> None:
    document = "query A { viewer { login } } mutation B { deleteRef(input: {}) { a } }"

    for operation_name in (None, "A"):
        reason = _rejection(_body(document, operationName=operation_name))
        assert "2 operations" in reason


def test_json_array_batch_is_refused() -> None:
    body = json.dumps([{"query": "{ viewer { login } }"}, {"query": "{ viewer { id } }"}])

    assert "batch" in _rejection(body.encode())


def test_persisted_query_is_refused() -> None:
    body = json.dumps(
        {"extensions": {"persistedQuery": {"version": 1, "sha256Hash": "abc"}}}
    ).encode()

    assert "persisted" in _rejection(body)


def test_persisted_query_with_a_query_is_refused() -> None:
    body = _body("{ viewer { login } }", extensions={"persistedQuery": {"version": 1}})

    assert "persisted" in _rejection(body)


def test_other_extensions_are_refused() -> None:
    assert "unsupported key" in _rejection(_body("{ viewer { login } }", extensions={"x": 1}))


@pytest.mark.parametrize(
    "body",
    [
        b'{"query": "{ viewer { login } }", "query": "mutation { deleteRef { a } }"}',
        b'{"query": "{ viewer { login } }", "variables": {"a": 1, "a": 2}}',
    ],
)
def test_duplicate_json_keys_at_any_depth_are_refused(body: bytes) -> None:
    assert "duplicate key" in _rejection(body)


def test_subscription_is_refused() -> None:
    assert "subscription" in _rejection(_body("subscription { event { id } }"))


@pytest.mark.parametrize(
    "document",
    [
        "mutation { ... on Query { viewer { login } } }",
        "query { ... on Mutation { deleteRef(input: {}) { a } } }",
        "mutation { ...F } fragment F on PullRequest { id }",
        "query { ...F } fragment F on Mutation { deleteRef(input: {}) { a } }",
    ],
)
def test_root_fragment_on_another_type_is_refused(document: str) -> None:
    assert "root type" in _rejection(_body(document))


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("mutation { ...Missing }", "undefined fragment"),
        ("mutation { ...A } fragment A on Mutation { ...A }", "spreads itself"),
        (
            "mutation { ...A } fragment A on Mutation { ...B } fragment B on Mutation { ...A }",
            "spreads itself",
        ),
        (
            "mutation { ...A } fragment A on Mutation { a } fragment A on Mutation { b }",
            "more than once",
        ),
        ("type Query { a: Int } query { a }", "type-system definition"),
        ("fragment A on Query { a }", "0 operations"),
    ],
)
def test_malformed_documents_are_refused(document: str, expected: str) -> None:
    assert expected in _rejection(_body(document))


@pytest.mark.parametrize(
    "document", ["query {", "mutation { deleteRef(input: { }", "{ viewer { login } } }", "  "]
)
def test_unparsable_document_is_refused_without_echoing_it(document: str) -> None:
    reason = _rejection(_body(document))

    assert "query" in reason
    assert "deleteRef" not in reason


def test_operation_name_must_name_the_operation() -> None:
    reason = _rejection(_body("query A { viewer { login } }", operationName="B"))

    assert "operationName" in reason


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, "no body"),
        (b"", "no body"),
        (b"not json", "not a UTF-8 JSON"),
        (b"\xff\xfe{}", "not a UTF-8 JSON"),
        (b'"a string"', "JSON object"),
        (b"{}", "no 'query'"),
        (b'{"query": 1}', "no 'query'"),
        (b'{"query": "{ a }", "variables": [1]}', "'variables'"),
        (b'{"query": "{ a }", "operationName": 1}', "'operationName'"),
        (b'{"query": "{ a }", "id": "abc"}', "unsupported key"),
    ],
)
def test_malformed_bodies_are_refused(body: bytes | None, expected: str) -> None:
    assert expected in _rejection(body)


def test_document_over_the_token_budget_is_refused() -> None:
    fields = " ".join(f"f{i}" for i in range(MAX_DOCUMENT_TOKENS))

    assert "not a valid GraphQL document" in _rejection(_body(f"{{ {fields} }}"))


def test_deeply_nested_json_is_refused() -> None:
    body = b'{"query": "{ a }", "variables": {"v": ' + b"[" * 100_000 + b"]" * 100_000 + b"}}"

    assert isinstance(_inspect(body), GraphQLRejection)


def test_deeply_nested_fragments_are_refused_not_raised() -> None:
    count = 1_500
    fragments = " ".join(f"fragment F{i} on Mutation {{ ...F{i + 1} }}" for i in range(count))
    document = f"mutation {{ ...F0 }} {fragments} fragment F{count} on Mutation {{ a }}"

    assert "nested too deeply" in _rejection(_body(document))


def test_fragments_spread_many_times_are_expanded_once_each() -> None:
    # Each fragment spreads the next twice: 2**40 expansions if not memoized.
    depth = 40
    fragments = " ".join(
        f"fragment F{i} on Mutation {{ ...F{i + 1} ...F{i + 1} }}" for i in range(depth)
    )
    document = (
        f"mutation {{ ...F0 }} {fragments} fragment F{depth} on Mutation {{ deleteRef {{ a }} }}"
    )

    assert _call(document).top_level_fields == ("deleteRef",)
