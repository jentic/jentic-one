"""Integration tests: ingested server variables resolve request URLs.

Ingests real specs whose servers declare variables (an enum path variable, an
enum host variable, a free-form host variable) through the real ``Ingestor``,
then resolves request URLs with ``RegistryService.resolve_operation`` against
the real Registry DB. Every enum value and the templated ``{name}`` form must
route to the operation, and the resolved ``server_variables`` must carry the
concrete values of the request URL.
"""

from __future__ import annotations

from typing import Any

import pytest

from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.inspect.registry_service import RegistryService
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


def _spec(*, vendor: str, server: dict[str, Any]) -> IngestSpecification:
    return IngestSpecification(
        api_identifier=ApiIdentifier(
            vendor=vendor, name="widgets", version="1.0.0", filename="spec.yaml"
        ),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.1.0",
            "info": {"title": "Widgets", "version": "1.0.0"},
            "servers": [server],
            "paths": {
                "/widgets": {
                    "get": {
                        "operationId": "listWidgets",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/widgets/{widgetId}": {
                    "get": {
                        "operationId": "getWidget",
                        "parameters": [
                            {
                                "name": "widgetId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        },
        sha=f"sha-{vendor}",
        origin="catalog",
    )


_REGION_PATH_SERVER = {
    "url": "https://widgets.example.com/{region}",
    "variables": {"region": {"default": "us", "enum": ["us", "eu"]}},
}


@pytest.fixture()
async def region_path_api(ingest_context: Context, clean_registry: None) -> Context:
    await Ingestor(ingest_context).ingest(
        _spec(vendor="widgets-example-com", server=_REGION_PATH_SERVER), created_by="usr_test"
    )
    return ingest_context


@pytest.mark.parametrize(
    ("url", "values", "defaults"),
    [
        ("https://widgets.example.com/us/widgets", {"region": "us"}, {}),
        ("https://widgets.example.com/eu/widgets", {"region": "eu"}, {}),
        ("https://widgets.example.com/{region}/widgets", {}, {"region": "us"}),
        ("https://widgets.example.com/%7Bregion%7D/widgets", {}, {"region": "us"}),
    ],
)
async def test_enum_path_variable_routes_every_value(
    region_path_api: Context, url: str, values: dict[str, str], defaults: dict[str, str]
) -> None:
    async with region_path_api.registry_db.session() as session:
        result = await RegistryService(session).resolve_operation(method="GET", url=url)

    assert result is not None
    assert result.path_params == {}
    assert result.server_variables == values
    assert result.server_variable_defaults == defaults


async def test_enum_path_variable_keeps_operation_path_params(region_path_api: Context) -> None:
    async with region_path_api.registry_db.session() as session:
        result = await RegistryService(session).resolve_operation(
            method="GET", url="https://widgets.example.com/eu/widgets/w-42"
        )

    assert result is not None
    assert result.server_variables == {"region": "eu"}
    assert result.path_params == {"widgetId": "w-42"}


async def test_undeclared_enum_value_does_not_route(region_path_api: Context) -> None:
    async with region_path_api.registry_db.session() as session:
        result = await RegistryService(session).resolve_operation(
            method="GET", url="https://widgets.example.com/ap/widgets"
        )

    assert result is None


async def test_enum_host_variable_routes_every_value(
    ingest_context: Context, clean_registry: None
) -> None:
    server = {
        "url": "https://{region}.widgets.example.com/v1",
        "variables": {"region": {"default": "us", "enum": ["us", "eu"]}},
    }
    await Ingestor(ingest_context).ingest(
        _spec(vendor="widgets-example-com", server=server), created_by="usr_test"
    )

    async with ingest_context.registry_db.session() as session:
        svc = RegistryService(session)
        us = await svc.resolve_operation(
            method="GET", url="https://us.widgets.example.com/v1/widgets"
        )
        eu = await svc.resolve_operation(
            method="GET", url="https://eu.widgets.example.com/v1/widgets"
        )

    assert us is not None and us.server_variables == {"region": "us"}
    assert eu is not None and eu.server_variables == {"region": "eu"}


async def test_free_form_host_variable_matches_any_label(
    ingest_context: Context, clean_registry: None
) -> None:
    server = {
        "url": "https://{tenant}.widgets.example.com",
        "variables": {"tenant": {"default": "demo"}},
    }
    await Ingestor(ingest_context).ingest(
        _spec(vendor="widgets-example-com", server=server), created_by="usr_test"
    )

    async with ingest_context.registry_db.session() as session:
        svc = RegistryService(session)
        acme = await svc.resolve_operation(
            method="GET", url="https://acme.widgets.example.com/widgets"
        )
        demo = await svc.resolve_operation(
            method="GET", url="https://demo.widgets.example.com/widgets"
        )
        other = await svc.resolve_operation(
            method="GET", url="https://acme.other.example.org/widgets"
        )

    assert acme is not None and acme.server_variables == {"tenant": "acme"}
    assert demo is not None and demo.server_variables == {"tenant": "demo"}
    assert other is None
