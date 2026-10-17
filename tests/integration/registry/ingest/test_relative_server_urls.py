"""Integration tests: relative server URLs are resolved or refused at ingest (#1241).

Ingests specs through the real ``Ingestor`` against the real Registry DB and
checks every consumer of ``servers`` — the server rows, the URL index the broker
routes by, and the stored spec file — sees the same absolute URL. A host-less
revision ingested without the resolution stage (as rows written before it
existed are) stays unroutable: the broker's resolver never matches it, and the
documented remedy (re-import from the spec URL, then promote) makes it routable.
Catalog, update, and overlay re-ingests keep unresolvable servers inspect-only;
an overlay never resolves, so a host-less API only gains a host through the
re-import, which resolves against the post-redirect URL, and a promote that
needs ``credentials:write`` when credentials are bound.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.services.discovery import discover
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.exc import DuplicateRevisionError, IngestPipelineError
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.schemas import IngestResult
from jentic_one.registry.ingest.stages.resolve_servers import ResolveServerUrlsStage
from jentic_one.registry.repos.catalog_repo import CatalogRepository
from jentic_one.registry.services.errors import HostChangeRequiresOperatorError
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import (
    ActorType,
    ApiRevisionSourceType,
    ApiRevisionState,
    StoredCredentialType,
)
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration

PETSTORE_URL = "https://petstore3.swagger.io/api/v3/openapi.json"
MIRROR_URL = (
    "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/"
    "petstore3.swagger.io/main/1.0.27/openapi.json"
)
_WRITER = Identity(sub="usr_writer", actor_type=ActorType.USER, permissions=["apis:write"])
_OPERATOR = Identity(
    sub="usr_operator",
    actor_type=ActorType.USER,
    permissions=["apis:write", "credentials:write"],
)
_AGENT_NAME = "relative-servers-agent"


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


async def _ingest_legacy(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, spec: IngestSpecification
) -> IngestResult:
    """Ingest ``spec`` without server resolution, as revisions stored before it were."""

    async def _skip(self: ResolveServerUrlsStage, ctx: Any) -> None:
        return None

    with monkeypatch.context() as patched:
        patched.setattr(ResolveServerUrlsStage, "_run", _skip)
        return await Ingestor(ctx).ingest(spec, created_by="usr_test")


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
    assert result.server_url_changes is not None
    assert result.server_url_changes.resolved == ["https://petstore3.swagger.io/api/v3"]

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
    with pytest.raises(IngestPipelineError, match=r"has no base; use an absolute URL: '/api/v3'"):
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

    result = await _ingest_legacy(
        ingest_context,
        monkeypatch,
        _petstore(source_type=ApiRevisionSourceType.INLINE, source_filename="petstore.json"),
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


async def _promote(ctx: Context, result: IngestResult, identity: Identity | None = None) -> None:
    await RevisionService(ctx).promote(
        result.api_vendor,
        result.api_name,
        result.api_version,
        str(result.revision_id),
        identity=identity or _WRITER,
    )


async def _state(ctx: Context, result: IngestResult) -> str:
    async with ctx.registry_db.session() as session:
        revision = await session.get(ApiRevision, result.revision_id)
        assert revision is not None
        return revision.state


async def test_reimporting_a_legacy_host_less_api_from_its_url_makes_it_routable(
    ingest_context: Context, clean_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the troubleshooting runbook: a live API stored with host-less servers
    is fixed by importing the same, unchanged document from its URL again and
    promoting the new draft. The unchanged bytes must not collide with the live
    revision (``DuplicateRevisionError``): the new revision stores resolved
    servers, so its digest differs."""
    legacy = await _ingest_legacy(
        ingest_context,
        monkeypatch,
        _petstore(source_type=ApiRevisionSourceType.URL, source_url=PETSTORE_URL),
    )
    await _promote(ingest_context, legacy)
    resolver = InProcessRegistryResolver(ingest_context.registry_db)
    pet = "https://petstore3.swagger.io/api/v3/pet/7"
    assert await discover(resolver, method="GET", url=pet) is None

    reimport = await Ingestor(ingest_context).ingest(
        _petstore(source_type=ApiRevisionSourceType.URL, source_url=PETSTORE_URL),
        created_by="usr_test",
    )
    assert reimport.revision_id != legacy.revision_id
    assert reimport.state == ApiRevisionState.DRAFT
    await _promote(ingest_context, reimport)

    resolved = await discover(resolver, method="GET", url=pet)
    assert resolved is not None
    assert resolved.path_params == {"petId": "7"}
    assert await _state(ingest_context, legacy) == ApiRevisionState.ARCHIVED

    # The resolved content is now the revision of record: importing it again is
    # the ordinary duplicate.
    with pytest.raises(IngestPipelineError, match=re.escape(DuplicateRevisionError().message)):
        await Ingestor(ingest_context).ingest(
            _petstore(source_type=ApiRevisionSourceType.URL, source_url=PETSTORE_URL),
            created_by="usr_test",
        )


async def test_manual_url_import_from_a_content_host_is_refused_and_writes_nothing(
    ingest_context: Context, clean_registry: None
) -> None:
    """A manual import of the catalog mirror's URL must not resolve ``/api/v3``
    to ``raw.githubusercontent.com``, where credentials bound to it would go."""
    with pytest.raises(IngestPipelineError, match="spec host serves files"):
        await Ingestor(ingest_context).ingest(
            _petstore(source_type=ApiRevisionSourceType.URL, source_url=MIRROR_URL),
            created_by="usr_test",
        )

    async with ingest_context.registry_db.session() as session:
        assert (await session.execute(select(ApiRevision))).first() is None


async def test_catalog_import_and_update_of_relative_servers_stay_inspect_only(
    ingest_context: Context, clean_registry: None
) -> None:
    """Catalog imports and their update re-imports never resolve against the
    mirror; a relative-only spec imports inspect-only with its servers as
    declared and its raw digest (the update check compares raw digests)."""
    catalog = {
        "source_type": ApiRevisionSourceType.URL,
        "source_url": MIRROR_URL,
        "origin": "catalog",
        "catalog_api_id": "petstore3.swagger.io",
    }
    first = await Ingestor(ingest_context).ingest(_petstore(**catalog), created_by="usr_test")

    servers, hosts, stored = await _revision_rows(ingest_context)
    assert servers == ["/api/v3"]
    assert hosts == [""]
    assert stored["servers"] == [{"url": "/api/v3"}]
    assert first.state == ApiRevisionState.IMPORTED
    assert first.server_url_changes is not None
    assert first.server_url_changes.unresolved == ["/api/v3"]
    async with ingest_context.registry_db.session() as session:
        revision = await session.get(ApiRevision, first.revision_id)
        assert revision is not None
        assert revision.spec_digest == "sha-petstore-relative"

    update = _petstore(**catalog)
    update.sha = "sha-petstore-relative-upstream-changed"
    second = await Ingestor(ingest_context).ingest(update, created_by="usr_test")

    assert second.state == ApiRevisionState.IMPORTED
    assert await _state(ingest_context, first) == ApiRevisionState.ARCHIVED


async def test_overlay_over_a_legacy_host_less_inline_api_stays_inspect_only(
    ingest_context: Context, clean_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-materializing an overlay over an inline import has no base URL; it must
    not fail, and keeps the API inspect-only as it was."""
    legacy = await _ingest_legacy(
        ingest_context,
        monkeypatch,
        _petstore(source_type=ApiRevisionSourceType.INLINE, source_filename="petstore.json"),
    )
    await _promote(ingest_context, legacy)
    overlay = _petstore(source_type=ApiRevisionSourceType.INLINE, origin="overlay")
    overlay.sha = "sha-petstore-overlaid"

    result = await Ingestor(ingest_context).ingest(overlay, created_by="usr_test")

    assert result.state == ApiRevisionState.IMPORTED
    assert result.server_url_changes is not None
    assert result.server_url_changes.unresolved == ["/api/v3"]


@pytest.fixture()
async def bound_petstore(ingest_context: Context) -> AsyncGenerator[None, None]:
    """A credential covering the petstore API, bound to an agent."""

    async def _clean() -> None:
        async with ingest_context.admin_db.session() as session:
            agents = select(Agent.id).where(Agent.name == _AGENT_NAME)
            await session.execute(
                delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(agents))
            )
            await session.execute(delete(Agent).where(Agent.name == _AGENT_NAME))
            await session.commit()
        async with ingest_context.control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor == "swagger"))
            await session.commit()

    await _clean()
    credential_id = generate_ksuid("cred")
    async with ingest_context.control_db.session() as session:
        session.add(
            Credential(
                id=credential_id,
                type=StoredCredentialType.API_KEY,
                name="cred-petstore",
                api_vendor="swagger",
                api_name="petstore",
                api_version="1.0.27",
                created_by="usr_x",
            )
        )
        await session.commit()
    async with ingest_context.admin_db.session() as session:
        agent = Agent(name=_AGENT_NAME, registered_by="usr_x")
        session.add(agent)
        await session.flush()
        session.add(AgentCredentialBinding(agent_id=agent.id, credential_id=credential_id))
        await session.commit()
    yield
    await _clean()


async def test_overlay_over_a_legacy_host_less_url_api_never_gives_it_a_host(
    ingest_context: Context,
    clean_registry: None,
    bound_petstore: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overlay materialization carries the base's ``source_url``, but its
    confirmer never saw a host and no ``credentials:write`` review runs, so it
    keeps the servers as declared. The bound credentials go nowhere new."""
    legacy = await _ingest_legacy(
        ingest_context,
        monkeypatch,
        _petstore(source_type=ApiRevisionSourceType.URL, source_url=PETSTORE_URL),
    )
    await _promote(ingest_context, legacy, _OPERATOR)
    overlay = _petstore(
        source_type=ApiRevisionSourceType.INLINE, origin="overlay", source_url=PETSTORE_URL
    )
    overlay.sha = "sha-petstore-overlaid"

    result = await Ingestor(ingest_context).ingest(overlay, created_by="usr_test")

    assert result.state == ApiRevisionState.IMPORTED
    assert result.server_url_changes is not None
    assert result.server_url_changes.resolved == []
    assert result.server_url_changes.unresolved == ["/api/v3"]
    async with ingest_context.registry_db.session() as session:
        servers = (
            (
                await session.execute(
                    select(Server.url).where(Server.revision_id == result.revision_id)
                )
            )
            .scalars()
            .all()
        )
        hosts = (
            (
                await session.execute(
                    select(OperationURLIndex.host).where(
                        OperationURLIndex.revision_id == result.revision_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert list(servers) == ["/api/v3"]
    assert list(hosts) == [""]


async def test_promoting_a_reimport_of_a_credential_bound_host_less_api_needs_credentials_write(
    ingest_context: Context,
    clean_registry: None,
    bound_petstore: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the runbook's gate: the re-import resolves against the URL its
    redirects ended on, and gives the API hosts it never had, so with
    credentials bound a promote needs ``credentials:write``."""
    legacy = await _ingest_legacy(
        ingest_context,
        monkeypatch,
        _petstore(source_type=ApiRevisionSourceType.URL, source_url=PETSTORE_URL),
    )
    await _promote(ingest_context, legacy, _OPERATOR)

    reimport = await Ingestor(ingest_context).ingest(
        _petstore(
            source_type=ApiRevisionSourceType.URL,
            source_url=PETSTORE_URL,
            retrieval_url="https://cdn.petstore.example/api/v3/openapi.json",
        ),
        created_by="usr_test",
    )
    assert reimport.state == ApiRevisionState.DRAFT
    assert reimport.server_url_changes is not None
    assert reimport.server_url_changes.resolved == ["https://cdn.petstore.example/api/v3"]

    with pytest.raises(HostChangeRequiresOperatorError):
        await _promote(ingest_context, reimport)
    assert await _state(ingest_context, reimport) == ApiRevisionState.DRAFT
    assert await _state(ingest_context, legacy) != ApiRevisionState.ARCHIVED

    await _promote(ingest_context, reimport, _OPERATOR)
    assert await _state(ingest_context, legacy) == ApiRevisionState.ARCHIVED
    resolver = InProcessRegistryResolver(ingest_context.registry_db)
    resolved = await discover(
        resolver, method="GET", url="https://cdn.petstore.example/api/v3/pet/7"
    )
    assert resolved is not None


@pytest.fixture()
async def catalog_on_a_spec_cdn(ingest_context: Context) -> AsyncGenerator[str, None]:
    """A cached manifest whose spec files live on a host other than the manifest's."""
    spec_url = "https://specs.cdn.example/apis/openapi/petstore3.swagger.io/openapi.json"

    async def _clean() -> None:
        async with ingest_context.registry_db.session() as session:
            await session.execute(delete(CatalogSnapshot))
            await session.commit()

    await _clean()
    async with ingest_context.registry_db.transaction() as session:
        await CatalogRepository.replace(
            session,
            source_url="https://manifest.example/apis.json",
            entries=[{"api_id": "petstore3.swagger.io", "spec_url": spec_url}],
        )
    yield spec_url
    await _clean()


async def test_manual_import_from_the_catalogs_spec_host_is_refused(
    ingest_context: Context, clean_registry: None, catalog_on_a_spec_cdn: str
) -> None:
    """The catalog mirror is every host the cached manifest serves spec files
    from, not only the configured manifest's host."""
    other_spec = "https://specs.cdn.example/apis/openapi/other.example/openapi.json"
    for url in (catalog_on_a_spec_cdn, other_spec):
        with pytest.raises(IngestPipelineError, match="spec host serves files"):
            await Ingestor(ingest_context).ingest(
                _petstore(source_type=ApiRevisionSourceType.URL, source_url=url),
                created_by="usr_test",
            )

    async with ingest_context.registry_db.session() as session:
        assert (await session.execute(select(ApiRevision))).first() is None
