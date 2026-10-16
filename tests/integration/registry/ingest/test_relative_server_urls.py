"""Integration tests: relative server URLs are resolved or refused at ingest (#1241).

Ingests specs through the real ``Ingestor`` against the real Registry DB and
checks every consumer of ``servers`` — the server rows, the URL index the broker
routes by, and the stored spec file — sees the same absolute URL. A host-less
revision ingested without the resolution stage (as rows written before it
existed are) stays unroutable: the broker's resolver never matches it.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.broker.services.discovery import discover
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.exc import IngestPipelineError
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.stages.resolve_servers import ResolveServerUrlsStage
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionSourceType
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration

PETSTORE_URL = "https://petstore3.swagger.io/api/v3/openapi.json"


def _petstore(**spec_fields: Any) -> IngestSpecification:
    return IngestSpecification(
        api_identifier=ApiIdentifier(vendor="swagger", name="petstore", version="1.0.27"),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.0.4",
            "info": {"title": "Swagger Petstore", "version": "1.0.27"},
            "servers": [{"url": "/api/v3"}],
            "paths": {
                "/pet/{petId}": {
                    "get": {
                        "operationId": "getPetById",
                        "parameters": [
                            {
                                "name": "petId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        sha="sha-petstore-relative",
        **spec_fields,
    )


async def _revision_rows(ctx: Context) -> tuple[list[str], list[str | None], dict[str, Any]]:
    async with ctx.registry_db.session() as session:
        servers = (await session.execute(select(Server.url))).scalars().all()
        hosts = (await session.execute(select(OperationURLIndex.host))).scalars().all()
        spec_file = (await session.execute(select(SpecFile))).unique().scalar_one()
        return list(servers), list(hosts), dict(spec_file.content)


async def test_url_import_resolves_relative_servers_against_the_spec_url(
    ingest_context: Context, clean_registry: None
) -> None:
    result = await Ingestor(ingest_context).ingest(
        _petstore(source_type=ApiRevisionSourceType.URL, source_url=PETSTORE_URL),
        created_by="usr_test",
    )

    servers, hosts, stored = await _revision_rows(ingest_context)
    assert servers == ["https://petstore3.swagger.io/api/v3"]
    assert hosts == ["petstore3.swagger.io"]
    assert stored["servers"] == [{"url": "https://petstore3.swagger.io/api/v3"}]

    resolver = InProcessRegistryResolver(ingest_context.registry_db)
    resolved = await discover(
        resolver,
        method="GET",
        url="https://petstore3.swagger.io/api/v3/pet/7",
        revision_id=result.revision_id,
    )
    assert resolved is not None
    assert resolved.path_params == {"petId": "7"}


async def test_inline_import_of_relative_servers_is_refused_and_writes_nothing(
    ingest_context: Context, clean_registry: None
) -> None:
    with pytest.raises(IngestPipelineError, match=r"relative server URL '/api/v3'"):
        await Ingestor(ingest_context).ingest(
            _petstore(source_type=ApiRevisionSourceType.INLINE, source_filename="petstore.json"),
            created_by="usr_test",
        )

    async with ingest_context.registry_db.session() as session:
        assert (await session.execute(select(ApiRevision))).first() is None
        assert (await session.execute(select(Server))).first() is None


async def test_host_less_revision_is_never_routed_by_the_broker(
    ingest_context: Context, clean_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revision stored with a relative server (written before ingest resolved
    them) has no host in its URL index, so no request URL resolves to it: the
    broker answers ``operation_not_found`` and never dispatches upstream. The
    execute clients (MCP ``execute``, ``jentic execute``) refuse such an
    operation up front with ``RESOLVE_FAILED``, naming its host-relative url."""

    async def _skip(self: ResolveServerUrlsStage, ctx: Any) -> None:
        return None

    monkeypatch.setattr(ResolveServerUrlsStage, "_run", _skip)
    result = await Ingestor(ingest_context).ingest(
        _petstore(source_type=ApiRevisionSourceType.INLINE, source_filename="petstore.json"),
        created_by="usr_test",
    )

    servers, hosts, _ = await _revision_rows(ingest_context)
    assert servers == ["/api/v3"]
    assert hosts == [""]

    resolver = InProcessRegistryResolver(ingest_context.registry_db)
    for url in (
        "https://petstore3.swagger.io/api/v3/pet/7",
        # What the broker reconstructs from a host-relative ``/api/v3/pet/7`` path.
        "https://api/v3/pet/7",
    ):
        assert (
            await discover(resolver, method="GET", url=url, revision_id=result.revision_id)
        ) is None
