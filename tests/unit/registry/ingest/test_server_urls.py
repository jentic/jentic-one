"""Tests for relative server URL resolution at ingest (#1241)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from jentic_one.registry.ingest.exc import IngestStageError, RelativeServerUrlError
from jentic_one.registry.ingest.models import (
    ApiIdentifier,
    IngestSpecification,
    SpecType,
)
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.server_urls import (
    is_hostless_server_url,
    resolve_relative_servers,
    server_base_url,
)
from jentic_one.registry.ingest.stages.resolve_servers import ResolveServerUrlsStage
from jentic_one.registry.ingest.stages.validation import ValidateOpenAPISpec
from jentic_one.shared.models import ApiRevisionSourceType

PETSTORE_URL = "https://petstore3.swagger.io/api/v3/openapi.json"


def _spec(servers: list[dict[str, Any]] | None, **paths: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "openapi": "3.0.4",
        "info": {"title": "Pets", "version": "1.0.0"},
        "paths": paths or {"/pet": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    if servers is not None:
        content["servers"] = servers
    return content


def _ctx(content: dict[str, Any], **spec_fields: Any) -> PipelineContext:
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="swagger", name="petstore", version="1.0.0"),
        content=content,
        **spec_fields,
    )
    return PipelineContext(session=None, specification=spec, created_by="usr_test")


@pytest.mark.parametrize(
    ("url", "variables", "expected"),
    [
        ("/api/v3", None, True),
        ("api/v3", None, True),
        ("", None, True),
        ("https://api.example.com/v1", None, False),
        ("//api.example.com/v1", None, False),
        ("https://{region}.example.com", {"region": {"default": "us"}}, False),
        ("/api/{version}", {"version": {"default": "v3"}}, True),
        ("{base}/v1", {"base": {"default": "https://api.example.com"}}, False),
        ("{base}/v1", {"base": {"default": "/api"}}, True),
        # No default: the leading variable may name a host — opaque, not host-less.
        ("{base}/v1", None, False),
    ],
)
def test_is_hostless_server_url(url: str, variables: Any, expected: bool) -> None:
    assert is_hostless_server_url(url, variables) is expected


@pytest.mark.parametrize(
    ("source_url", "expected"),
    [
        (PETSTORE_URL, PETSTORE_URL),
        (
            # pragma: allowlist nextline secret
            "https://user:secret@specs.example.com:8443/v1/openapi.yaml?token=abc#frag",
            "https://specs.example.com:8443/v1/openapi.yaml",
        ),
        ("https://specs.example.com", "https://specs.example.com/"),
        (None, None),
        ("", None),
        ("ftp://specs.example.com/openapi.yaml", None),
        ("/relative/openapi.yaml", None),
    ],
)
def test_server_base_url_drops_credentials_query_and_fragment(
    source_url: str | None, expected: str | None
) -> None:
    assert server_base_url(source_url) == expected


def test_relative_only_servers_resolve_against_the_document_url() -> None:
    content = _spec([{"url": "/api/v3", "description": "stock petstore"}])

    result = resolve_relative_servers(content, PETSTORE_URL)

    assert content["servers"] == [
        {"url": "https://petstore3.swagger.io/api/v3", "description": "stock petstore"}
    ]
    assert result.resolved == [("/api/v3", "https://petstore3.swagger.io/api/v3")]
    assert result.dropped == []


@pytest.mark.parametrize(
    ("relative", "absolute"),
    [
        ("v2", "https://specs.example.com/api/v2"),
        ("../v2", "https://specs.example.com/v2"),
        ("/", "https://specs.example.com/"),
    ],
)
def test_document_relative_paths_follow_rfc3986(relative: str, absolute: str) -> None:
    content = _spec([{"url": relative}])

    resolve_relative_servers(content, "https://specs.example.com/api/openapi.json")

    assert content["servers"][0]["url"] == absolute


def test_relative_only_servers_without_a_base_are_refused_naming_the_urls() -> None:
    content = _spec([{"url": "/api/v3"}, {"url": "/api/v4"}])

    with pytest.raises(RelativeServerUrlError) as excinfo:
        resolve_relative_servers(content, None)

    assert excinfo.value.urls == ["/api/v3", "/api/v4"]
    message = str(excinfo.value)
    assert message.startswith("relative server URL '/api/v3' (+1 more) has no base URL")
    assert "absolute server URL" in message
    # The job error column keeps 128 characters, including the per-source prefix.
    assert len("all 1 import source(s) failed: source[0]: " + message) <= 128


def test_mixed_servers_without_a_base_keep_the_absolute_ones() -> None:
    content = _spec([{"url": "https://petstore.example.com/v3"}, {"url": "/api/v3"}])

    result = resolve_relative_servers(content, None)

    assert content["servers"] == [{"url": "https://petstore.example.com/v3"}]
    assert result.dropped == ["/api/v3"]


def test_mixed_servers_with_a_base_resolve_the_relative_ones() -> None:
    content = _spec([{"url": "https://petstore.example.com/v3"}, {"url": "/api/v3"}])

    resolve_relative_servers(content, PETSTORE_URL)

    assert [s["url"] for s in content["servers"]] == [
        "https://petstore.example.com/v3",
        "https://petstore3.swagger.io/api/v3",
    ]


def test_path_and_operation_level_servers_are_resolved_or_refused() -> None:
    paths = {
        "/pet": {
            "servers": [{"url": "/path-level"}],
            "get": {
                "servers": [{"url": "/op-level"}],
                "responses": {"200": {"description": "ok"}},
            },
        }
    }
    content = _spec([{"url": "https://api.example.com"}], **paths)
    refused = copy.deepcopy(content)

    resolve_relative_servers(content, PETSTORE_URL)

    assert content["paths"]["/pet"]["servers"] == [
        {"url": "https://petstore3.swagger.io/path-level"}
    ]
    assert content["paths"]["/pet"]["get"]["servers"] == [
        {"url": "https://petstore3.swagger.io/op-level"}
    ]
    with pytest.raises(RelativeServerUrlError) as excinfo:
        resolve_relative_servers(refused, None)
    assert excinfo.value.urls == ["/path-level", "/op-level"]


def test_server_variables_survive_resolution() -> None:
    variables = {"version": {"default": "v3", "enum": ["v3", "v4"]}}
    content = _spec(
        [
            {"url": "/api/{version}", "variables": variables},
            {"url": "https://{region}.example.com", "variables": {"region": {"default": "eu"}}},
        ]
    )

    resolve_relative_servers(content, PETSTORE_URL)

    assert content["servers"] == [
        {"url": "https://petstore3.swagger.io/api/{version}", "variables": variables},
        {"url": "https://{region}.example.com", "variables": {"region": {"default": "eu"}}},
    ]


def test_leading_variable_with_a_relative_default_is_unresolvable() -> None:
    content = _spec([{"url": "{basePath}/v1", "variables": {"basePath": {"default": "/api"}}}])

    with pytest.raises(RelativeServerUrlError):
        resolve_relative_servers(content, PETSTORE_URL)


def test_spec_without_servers_is_left_alone() -> None:
    content = _spec(None)
    before = copy.deepcopy(content)

    result = resolve_relative_servers(content, PETSTORE_URL)

    assert content == before
    assert result.resolved == []


@pytest.mark.anyio
async def test_stage_resolves_a_url_import() -> None:
    ctx = _ctx(
        _spec([{"url": "/api/v3"}]),
        source_type=ApiRevisionSourceType.URL,
        source_url=PETSTORE_URL,
    )

    await ResolveServerUrlsStage().run(ctx)

    assert ctx.specification.content is not None
    assert ctx.specification.content["servers"] == [{"url": "https://petstore3.swagger.io/api/v3"}]


@pytest.mark.anyio
async def test_stage_refuses_an_inline_import() -> None:
    ctx = _ctx(_spec([{"url": "/api/v3"}]), source_type=ApiRevisionSourceType.INLINE)

    with pytest.raises(RelativeServerUrlError, match="'/api/v3'"):
        await ResolveServerUrlsStage().run(ctx)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "catalog_fields",
    [
        {"origin": "catalog", "catalog_api_id": "petstore3.swagger.io"},
        # An overlay re-ingest of a catalog spec carries the catalog identity.
        {"origin": "overlay", "catalog_api_id": "petstore3.swagger.io"},
    ],
)
async def test_stage_never_resolves_against_a_catalog_mirror(
    catalog_fields: dict[str, Any],
) -> None:
    ctx = _ctx(
        _spec([{"url": "/api/v3"}]),
        source_type=ApiRevisionSourceType.URL,
        source_url="https://raw.githubusercontent.com/jentic/jentic-public-apis/main/x.json",
        **catalog_fields,
    )

    with pytest.raises(RelativeServerUrlError):
        await ResolveServerUrlsStage().run(ctx)


@pytest.mark.anyio
async def test_swagger_20_without_host_is_refused_before_server_resolution() -> None:
    """Swagger 2.0 (where a missing ``host`` means "the document's host") is not
    an accepted spec format, so it can never yield host-less operations."""
    ctx = _ctx({"swagger": "2.0", "info": {"title": "Pets", "version": "1"}, "basePath": "/v2"})

    with pytest.raises(IngestStageError, match="openapi"):
        await ValidateOpenAPISpec().run(ctx)
