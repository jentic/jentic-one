"""Integration tests: ingested server variables resolve request URLs.

Ingests real specs whose servers declare variables (an enum path variable, an
enum host variable, a free-form host variable) through the real ``Ingestor``,
then resolves request URLs with ``RegistryService.resolve_operation`` against
the real Registry DB. Every enum value and the templated ``{name}`` form must
route to the operation, and the resolved ``server_variables`` must carry the
concrete values of the request URL. Rows written before server-variable
capture (no format marker) re-derive the values from the stored servers.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import (
    URL_INDEX_FORMAT_MARKER,
    build_index_entry,
    merge_paths,
    parse_server_url,
)
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
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


async def test_free_form_host_variable_routes_only_default_and_template(
    ingest_context: Context, clean_registry: None
) -> None:
    """An enum-less host variable never routes a caller-picked label."""
    server = {
        "url": "https://{tenant}.widgets.example.com",
        "variables": {"tenant": {"default": "demo"}},
    }
    await Ingestor(ingest_context).ingest(
        _spec(vendor="widgets-example-com", server=server), created_by="usr_test"
    )

    async with ingest_context.registry_db.session() as session:
        svc = RegistryService(session)
        demo = await svc.resolve_operation(
            method="GET", url="https://demo.widgets.example.com/widgets"
        )
        templated = await svc.resolve_operation(
            method="GET", url="https://{tenant}.widgets.example.com/widgets"
        )
        acme = await svc.resolve_operation(
            method="GET", url="https://acme.widgets.example.com/widgets"
        )

    assert demo is not None and demo.server_variables == {"tenant": "demo"}
    assert templated is not None
    assert templated.server_variables == {}
    assert templated.server_variable_defaults == {"tenant": "demo"}
    assert acme is None


async def _rewrite_index_as_legacy(ctx: Context, *, expanded_server_url: str) -> None:
    """Replace the index with the rows the pre-capture builder wrote.

    That builder indexed only the defaults-expanded server URL, with no
    server-variable groups and no format marker.
    """
    async with ctx.registry_db.session() as session:
        rows = (await session.execute(select(OperationURLIndex))).scalars().all()
        revision_id = rows[0].revision_id
        await session.execute(delete(OperationURLIndex))
        operations = (
            (await session.execute(select(Operation).where(Operation.revision_id == revision_id)))
            .unique()
            .scalars()
            .all()
        )
        parsed = parse_server_url(expanded_server_url)
        for op in operations:
            entry = build_index_entry(parsed.host, merge_paths(parsed.path, op.path), "https")
            entry.path_regex = re.compile(
                entry.path_regex.pattern.removeprefix(URL_INDEX_FORMAT_MARKER)
            )
            await UrlIndexRepository.upsert_entry(
                session,
                revision_id=revision_id,
                operation_id=op.id,
                method=op.method.upper(),
                entry=entry,
                created_by="usr_test",
            )
        await session.commit()


async def test_legacy_row_derives_server_variables_from_stored_servers(
    region_path_api: Context,
) -> None:
    """A row indexed before capture groups still resolves the URL's values."""
    await _rewrite_index_as_legacy(
        region_path_api, expanded_server_url="https://widgets.example.com/us"
    )

    async with region_path_api.registry_db.session() as session:
        svc = RegistryService(session)
        listed = await svc.resolve_operation(
            method="GET", url="https://widgets.example.com/us/widgets"
        )
        fetched = await svc.resolve_operation(
            method="GET", url="https://widgets.example.com/us/widgets/w-1"
        )

    assert listed is not None
    assert listed.server_variables == {"region": "us"}
    assert listed.server_variables_unresolved is False
    assert fetched is not None
    assert fetched.server_variables == {"region": "us"}
    assert fetched.path_params == {"widgetId": "w-1"}


async def test_legacy_row_without_a_stored_server_is_unresolved(
    ingest_context: Context, clean_registry: None
) -> None:
    """A path-level server is not stored: its legacy row fails closed."""
    spec = _spec(vendor="widgets-example-com", server={"url": "https://api.widgets.example.com"})
    assert spec.content is not None
    spec.content["paths"]["/widgets"]["servers"] = [_REGION_PATH_SERVER]
    await Ingestor(ingest_context).ingest(spec, created_by="usr_test")
    await _rewrite_index_as_legacy(
        ingest_context, expanded_server_url="https://widgets.example.com/us"
    )

    async with ingest_context.registry_db.session() as session:
        result = await RegistryService(session).resolve_operation(
            method="GET", url="https://widgets.example.com/us/widgets"
        )

    assert result is not None
    assert result.server_variables == {}
    assert result.server_variables_unresolved is True
