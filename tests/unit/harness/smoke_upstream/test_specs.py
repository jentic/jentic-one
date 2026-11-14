"""Self-tests for the multi-version and poisoned spec router."""

from __future__ import annotations

import json

from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.specs import (
    FUTURE_40_VERSION,
    MEDIA_TYPE_XML,
    MEDIA_TYPE_YAML,
    OPENAPI_30_VERSION,
    OPENAPI_31_VERSION,
    SWAGGER_20_VERSION,
)


async def test_openapi_31_version_field(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/openapi-3.1.json")
    assert response.status_code == 200
    body = response.json()
    assert body["openapi"] == OPENAPI_31_VERSION
    assert "webhooks" in body


async def test_openapi_30_version_field(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/openapi-3.0.json")
    assert response.status_code == 200
    assert response.json()["openapi"] == OPENAPI_30_VERSION


async def test_swagger_20_version_field(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/swagger-2.0.json")
    assert response.status_code == 200
    assert response.json()["swagger"] == SWAGGER_20_VERSION


async def test_future_40_version_field(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/future-4.0.json")
    assert response.status_code == 200
    assert response.json()["openapi"] == FUTURE_40_VERSION


async def test_poison_infinite_loop_is_yaml_with_ref_cycle(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/poison/infinite-loop.yaml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(MEDIA_TYPE_YAML)
    assert "SchemaA" in response.text
    assert response.text.count("$ref") == 2


async def test_poison_billion_laughs_is_xml_with_entities(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/poison/billion-laughs.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(MEDIA_TYPE_XML)
    assert "&lol3;" in response.text


async def test_poison_massive_is_parseable_json(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/specs/poison/massive.json")
    assert response.status_code == 200
    parsed = json.loads(response.content)
    assert parsed["openapi"] == OPENAPI_31_VERSION
    assert len(parsed["components"]["schemas"]) > 1000
