"""Tests for search endpoint spec conformance — snake_case, spec-shaped response."""

from __future__ import annotations

import pytest

from jentic_one.registry.services.search_service import (
    ApiRef,
    OperationResult,
    _build_inspect_link,
)
from jentic_one.registry.web.schemas.apis import ApiReferenceResponse
from jentic_one.registry.web.schemas.search import (
    OperationResultResponse,
    SearchLinksResponse,
    SearchRequest,
    SearchResponse,
)


def test_search_request_accepts_query_without_max_length() -> None:
    long_query = "a" * 2000
    req = SearchRequest(query=long_query)
    assert req.query == long_query


def test_search_request_requires_min_length() -> None:
    with pytest.raises(ValueError):
        SearchRequest(query="")


def test_search_request_accepts_revision_pins() -> None:
    req = SearchRequest(
        query="test",
        revision_pins={"stripe:payments:2023-10-16": "rev_01HMY1Q0AB"},
    )
    assert req.revision_pins == {"stripe:payments:2023-10-16": "rev_01HMY1Q0AB"}


def test_search_request_apis_accepts_colon_tuples() -> None:
    req = SearchRequest(query="test", apis=["stripe:payments:2023-10-16", "stripe"])
    assert req.apis == ["stripe:payments:2023-10-16", "stripe"]


def test_operation_result_response_serializes_snake_case() -> None:
    result = OperationResultResponse(
        type="operation",
        api=ApiReferenceResponse(
            vendor="stripe", name="payments", version="2023-10-16", host="api.stripe.com"
        ),
        operation_id="op_abc123",
        method="POST",
        url="https://api.stripe.com/v1/payment_intents",
        target="POST:https://api.stripe.com/v1/payment_intents",
        name="Create a PaymentIntent",
        description="Creates a new payment intent",
        relevance_score=0.91,
        links=SearchLinksResponse(
            inspect="/inspect?id=POST%20https%3A//api.stripe.com/v1/payment_intents"
        ),
    )
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert data["type"] == "operation"
    assert "api" in data
    assert data["api"]["vendor"] == "stripe"
    assert data["api"]["name"] == "payments"
    assert data["api"]["version"] == "2023-10-16"
    assert data["api"]["host"] == "api.stripe.com"
    assert data["operation_id"] == "op_abc123"
    assert data["method"] == "POST"
    assert data["url"] == "https://api.stripe.com/v1/payment_intents"
    assert data["relevance_score"] == 0.91
    assert "_links" in data
    assert "inspect" in data["_links"]


def test_operation_result_response_no_camel_case_fields() -> None:
    result = OperationResultResponse(
        type="operation",
        api=ApiReferenceResponse(vendor="v", name="n", version="1", host=None),
        operation_id="op_1",
        method="GET",
        url="/test",
        target="op_1",
        name=None,
        description=None,
        relevance_score=0.5,
        links=SearchLinksResponse(inspect="/inspect?id=GET%20%2Ftest"),
    )
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    camel_fields = {
        "operationId",
        "relevanceScore",
        "revisionId",
        "apiId",
        "hasMore",
        "nextCursor",
    }
    assert not camel_fields.intersection(data.keys())


def test_operation_result_response_has_required_spec_fields() -> None:
    result = OperationResultResponse(
        type="operation",
        api=ApiReferenceResponse(vendor="v", name="n", version="1", host=None),
        operation_id="op_1",
        method="GET",
        url="/test",
        target="op_1",
        name=None,
        description=None,
        relevance_score=0.5,
        links=SearchLinksResponse(inspect="/inspect?id=GET%20%2Ftest"),
    )
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    required_fields = {
        "type",
        "api",
        "operation_id",
        "method",
        "url",
        "target",
        "relevance_score",
        "_links",
    }
    assert required_fields.issubset(data.keys())


def test_operation_result_response_no_leaked_revision_id_or_api_id() -> None:
    result = OperationResultResponse(
        type="operation",
        api=ApiReferenceResponse(vendor="v", name="n", version="1", host=None),
        operation_id="op_1",
        method="GET",
        url="/test",
        target="op_1",
        name=None,
        description=None,
        relevance_score=0.5,
        links=SearchLinksResponse(inspect="/inspect?id=test"),
    )
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    forbidden_fields = {"revision_id", "api_id", "path", "revisionId", "apiId"}
    assert not forbidden_fields.intersection(data.keys())


def test_search_response_envelope_snake_case() -> None:
    response = SearchResponse(
        data=[],
        has_more=True,
        next_cursor="abc123",
    )
    data = response.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "has_more" in data
    assert "next_cursor" in data
    assert "hasMore" not in data
    assert "nextCursor" not in data


def test_inspect_link_uses_id_query_param() -> None:
    link = _build_inspect_link("POST", "https://api.stripe.com/v1/payment_intents")
    assert link.startswith("/inspect?id=")
    assert "POST" in link
    assert "operation_id" not in link
    assert "revision_id" not in link


def test_inspect_link_encodes_method_and_url() -> None:
    link = _build_inspect_link("GET", "https://api.example.com/users/{id}")
    assert "GET%20https" in link


def test_operation_result_dataclass_has_type_field() -> None:
    result = OperationResult(
        type="operation",
        operation_id="op_1",
        method="GET",
        url="https://example.com/users",
        name="List users",
        description=None,
        relevance_score=0.8,
        api=ApiRef(vendor="example", name="users", version="1.0", host="example.com"),
        inspect_link="/inspect?id=GET%20https%3A//example.com/users",
        target="GET:https://example.com/users",
    )
    assert result.type == "operation"
    assert result.url == "https://example.com/users"
    assert result.api.vendor == "example"
