"""Integration tests for the ImportHandler end-to-end against real databases."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.exc import IngestJobError
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

MINIMAL_OPENAPI = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "summary": "List items",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
)


@pytest.fixture()
async def _clean_registry(registry_db: DatabaseSession) -> Any:
    """Truncate registry tables before and after."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(SecuritySchemeFlow))
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(delete(SpecFile))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def test_reimport_identical_content_is_idempotent(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """Re-importing identical content reuses the draft slot instead of colliding.

    Reproduces #688: the first import commits a draft revision; without the fix
    a second import of the same (api_id, spec_digest) collides with
    uq_api_revisions_api_id_spec_digest and fails forever.
    """
    handler = ImportHandler(integration_context)
    payload = {
        "sources": [
            {
                "type": "inline",
                "content": MINIMAL_OPENAPI,
                "filename": "openapi.json",
                "vendor": "dup-vendor",
                "api_name": "dup-api",
                "version": "1.0.0",
            }
        ]
    }

    first = await handler.execute(
        job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_test"
    )
    first_revision_id = first.body["revisions"][0]["revision_id"]

    # Re-import the exact same content — must succeed, not raise.
    second = await handler.execute(
        job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_test"
    )
    second_revision = second.body["revisions"][0]
    assert second_revision["state"] == "draft"
    # The leftover draft was replaced, so a fresh revision id is produced.
    assert second_revision["revision_id"] != first_revision_id

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1
        assert rows[0].state == "draft"
        assert str(rows[0].id) == second_revision["revision_id"]


async def test_reimport_after_sibling_failure_is_idempotent(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A draft left behind by a partially-failed job can be re-imported cleanly.

    First job: one good source (commits a draft) plus one bad source (fails).
    Second job re-imports the identical good source and must succeed.
    """
    handler = ImportHandler(integration_context)
    good_source = {
        "type": "inline",
        "content": MINIMAL_OPENAPI,
        "filename": "openapi.json",
        "vendor": "recover-vendor",
        "api_name": "recover-api",
        "version": "1.0.0",
    }
    bad_source = {
        "type": "inline",
        "content": "not valid json or yaml {{{{",
        "filename": "bad.json",
    }

    await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={"sources": [good_source, bad_source]},
        created_by="usr_test",
    )

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1  # the good source's draft survived the sibling failure

    # Re-importing the same good content must not collide.
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={"sources": [good_source]},
        created_by="usr_test",
    )
    assert result.body["revisions"][0]["state"] == "draft"

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1


async def test_reimport_over_active_revision_surfaces_readable_error(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """An active revision with identical content yields a readable error, not raw SQL.

    A promoted (published/imported) revision must not be silently overwritten;
    re-importing identical content collides and the failure message shown to the
    user must be the human-readable one, not a truncated SQLAlchemy string.
    """
    handler = ImportHandler(integration_context)
    payload = {
        "sources": [
            {
                "type": "inline",
                "content": MINIMAL_OPENAPI,
                "filename": "openapi.json",
                "vendor": "active-vendor",
                "api_name": "active-api",
                "version": "1.0.0",
            }
        ]
    }

    first = await handler.execute(
        job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_test"
    )
    revision_id = uuid.UUID(first.body["revisions"][0]["revision_id"])

    # Promote the draft to published so it is no longer a replaceable slot.
    async with registry_db.session() as session:
        await session.execute(
            update(ApiRevision).where(ApiRevision.id == revision_id).values(state="published")
        )
        await session.commit()

    with pytest.raises(IngestJobError) as exc_info:
        await handler.execute(
            job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_test"
        )

    message = str(exc_info.value)
    assert "identical content already exists" in message
    assert "uq_api_revisions" not in message
    assert "IntegrityError" not in message


async def test_execute_inline_source(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A valid inline source produces a revision with state=draft."""
    handler = ImportHandler(integration_context)
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": MINIMAL_OPENAPI,
                    "filename": "openapi.json",
                    "vendor": "test-vendor",
                    "api_name": "test-api",
                    "version": "1.0.0",
                }
            ]
        },
        created_by="usr_test",
    )

    revisions = result.body["revisions"]
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev["state"] == "draft"
    assert rev["api"]["vendor"] == "test-vendor"
    assert rev["api"]["name"] == "test-api"
    assert rev["api"]["version"] == "1.0.0"
    uuid.UUID(rev["revision_id"])

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1
        assert rows[0].state == "draft"


async def test_partial_failure_skips_bad_source(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """One valid and one malformed source: only the valid one produces a revision."""
    handler = ImportHandler(integration_context)
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": MINIMAL_OPENAPI,
                    "filename": "openapi.json",
                    "vendor": "good-vendor",
                    "api_name": "good-api",
                    "version": "2.0.0",
                },
                {
                    "type": "inline",
                    "content": "this is not valid json or yaml {{{{",
                    "filename": "bad.json",
                },
            ]
        },
        created_by="usr_test",
    )

    revisions = result.body["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["api"]["vendor"] == "good-vendor"

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1


async def test_all_sources_failing_raises(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """When every source fails, the handler raises so the job is marked failed."""
    handler = ImportHandler(integration_context)
    with pytest.raises(IngestJobError, match=r"all .* source.*failed"):
        await handler.execute(
            job_id=str(uuid.uuid4()),
            session=None,
            payload={
                "sources": [
                    {
                        "type": "inline",
                        "content": "not valid json or yaml {{{{",
                        "filename": "bad.json",
                    },
                ]
            },
            created_by="usr_test",
        )

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 0


_BASE_WITH_SERVERS = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "Overlay Base", "version": "1.0.0"},
        "servers": [{"url": "https://old.example.com"}],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
)

# A remove-then-set overlay applied to _BASE_WITH_SERVERS: rewrite servers.
_OVERLAY_DOC = {
    "overlay": "1.0.0",
    "actions": [
        {"target": "$.servers", "remove": True},
        {"target": "$", "update": {"servers": [{"url": "https://new.example.com"}]}},
    ],
}


async def _import_base(
    handler: ImportHandler, *, vendor: str, name: str, version: str, origin: str
) -> uuid.UUID:
    """Import a base spec with the given origin (imported + promoted to current)."""
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": _BASE_WITH_SERVERS,
                    "filename": "openapi.json",
                    "vendor": vendor,
                    "api_name": name,
                    "version": version,
                    "origin": origin,
                    "source_url": "https://catalog.example.com/base.json",
                }
            ]
        },
        created_by="usr_test",
    )
    return uuid.UUID(result.body["revisions"][0]["revision_id"])


async def test_overlay_materialization_rewrites_served_spec_and_links_revision(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A materialize job (origin=overlay + overlay_id) rewrites the served spec.

    Reproduces the Flow-3 confirm path: import a base (catalog) revision, create a
    pending overlay, then run the materialize job the confirm would enqueue. The new
    revision must become current, serve the overlaid servers, archive the old base,
    and back-link overlays.confirmed_revision_id.
    """
    handler = ImportHandler(integration_context)
    base_revision_id = await _import_base(
        handler, vendor="ovl-vendor", name="ovl-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "ovl-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id,
            document=_OVERLAY_DOC,
            status="pending",
            created_by="usr_test",
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    overlaid_content = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Overlay Base", "version": "1.0.0"},
            "servers": [{"url": "https://new.example.com"}],
            "paths": {
                "/items": {
                    "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}
                }
            },
        }
    )
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": overlaid_content,
                    "filename": "openapi.json",
                    "vendor": "ovl-vendor",
                    "api_name": "ovl-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )
    new_revision_id = uuid.UUID(result.body["revisions"][0]["revision_id"])
    assert result.body["revisions"][0]["state"] == "imported"
    assert new_revision_id != base_revision_id
    # The materialize superseded the served base revision — the result carries it and
    # it is back-linked onto the overlay for a later deterministic rollback (A5b).
    assert result.body["revisions"][0]["superseded_revision_id"] == str(base_revision_id)

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == new_revision_id

        base_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        assert base_rev.state == "archived"

        new_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == new_revision_id))
        ).scalar_one()
        assert new_rev.origin == "overlay"
        assert new_rev.source_url == "https://catalog.example.com/base.json"

        spec_file = (
            await session.execute(select(SpecFile).where(SpecFile.revision_id == new_revision_id))
        ).scalar_one()
        assert spec_file.content["servers"] == [{"url": "https://new.example.com"}]

        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.confirmed_revision_id == new_revision_id
        assert overlay_row.superseded_revision_id == base_revision_id


async def test_overlay_materialization_archives_active_revision_of_other_origin(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """origin=overlay must supersede an active revision even of a different origin.

    The one-active partial unique index (state IN ('published','imported')) allows a
    single active revision per API. An overlay revision archives the active base
    regardless of its origin, so the insert does not violate the index.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="diff-vendor", name="diff-api", version="1.0.0", origin="imported"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "diff-vendor"))).scalar_one()
        api_id = api.id

    overlaid_content = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Overlay Base", "version": "1.0.0"},
            "servers": [{"url": "https://overlaid.example.com"}],
            "paths": {
                "/items": {
                    "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}
                }
            },
        }
    )
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": overlaid_content,
                    "filename": "openapi.json",
                    "vendor": "diff-vendor",
                    "api_name": "diff-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                }
            ],
        },
        created_by="usr_test",
    )
    assert result.body["revisions"][0]["state"] == "imported"

    async with registry_db.session() as session:
        active = (
            (
                await session.execute(
                    select(ApiRevision).where(
                        ApiRevision.api_id == api_id,
                        ApiRevision.state.in_(["published", "imported"]),
                    )
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert len(active) == 1
        assert active[0].origin == "overlay"


async def test_overlay_materialization_supersedes_published_base(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """origin=overlay must supersede a manually-PROMOTED (published) base revision.

    The one-active partial unique index covers state IN ('published','imported'). If the
    base being overlaid was promoted to PUBLISHED, an origin-scoped or imported-only
    archive leaves it active and the new imported overlay revision violates the index.
    archive_all_active must archive the published base too. Regression for review H1.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="pub-vendor", name="pub-api", version="1.0.0", origin="imported"
    )

    # Promote the imported base to PUBLISHED and make it the API's current revision,
    # simulating an operator publish before any overlay is confirmed.
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "pub-vendor"))).scalar_one()
        api_id = api.id
        base_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.api_id == api_id))
        ).scalar_one()
        base_rev.state = "published"
        api.current_revision_id = base_rev.id
        await session.commit()
        base_rev_id = base_rev.id

    overlaid_content = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Pub Base", "version": "1.0.0"},
            "servers": [{"url": "https://overlaid.example.com"}],
            "paths": {
                "/items": {
                    "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}
                }
            },
        }
    )
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        session=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": overlaid_content,
                    "filename": "openapi.json",
                    "vendor": "pub-vendor",
                    "api_name": "pub-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                }
            ],
        },
        created_by="usr_test",
    )
    assert result.body["revisions"][0]["state"] == "imported"

    async with registry_db.session() as session:
        active = (
            (
                await session.execute(
                    select(ApiRevision).where(
                        ApiRevision.api_id == api_id,
                        ApiRevision.state.in_(["published", "imported"]),
                    )
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert len(active) == 1
        assert active[0].origin == "overlay"
        # The previously-published base is now archived.
        archived_base = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_rev_id))
        ).scalar_one()
        assert archived_base.state == "archived"


async def test_overlay_materialization_recovers_link_on_duplicate_reingest(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A re-run materialize job whose content already exists re-links the overlay.

    Models the H2 recovery window where a prior confirm produced the overlay revision
    but the confirmed_revision_id back-link was lost. Re-confirming enqueues the same
    overlaid content; the re-ingest raises DuplicateRevisionError, but the handler must
    resolve the existing overlay revision and re-link it — and NOT fail the job.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="rec-vendor", name="rec-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "rec-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="u")
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    overlaid_content = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Overlay Base", "version": "1.0.0"},
            "servers": [{"url": "https://new.example.com"}],
            "paths": {
                "/items": {
                    "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}
                }
            },
        }
    )
    job_payload = {
        "sources": [
            {
                "type": "inline",
                "content": overlaid_content,
                "filename": "openapi.json",
                "vendor": "rec-vendor",
                "api_name": "rec-api",
                "version": "1.0.0",
                "origin": "overlay",
                "source_url": "https://catalog.example.com/base.json",
            }
        ],
        "overlay_id": overlay_id,
    }

    # First materialize job: produces the overlay revision + link.
    first = await handler.execute(
        job_id=str(uuid.uuid4()), session=None, payload=job_payload, created_by="usr_test"
    )
    materialized_rev = uuid.UUID(first.body["revisions"][0]["revision_id"])
    superseded_rev = uuid.UUID(first.body["revisions"][0]["superseded_revision_id"])

    # Simulate the torn state: a confirm that committed the re-ingest (revision exists,
    # base archived) but crashed before back-linking — confirmed_revision_id AND
    # superseded_revision_id are both NULL.
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_id)
            .values(confirmed_revision_id=None, superseded_revision_id=None)
        )
        await session.commit()

    # Second (recovery) job with identical content: re-ingest dedupes, handler recovers.
    second = await handler.execute(
        job_id=str(uuid.uuid4()), session=None, payload=job_payload, created_by="usr_test"
    )
    # No new revision produced, but the job did not fail.
    assert second.body["revisions"] == []

    async with registry_db.session() as session:
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.confirmed_revision_id == materialized_rev
        # M1: recovery reconstructs the superseded revision (newest archived non-overlay
        # revision) so the torn state doesn't permanently strand A5b's rollback target.
        assert overlay_row.superseded_revision_id == superseded_rev


async def test_url_source_via_mock(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """URL source succeeds when the HTTP fetch is mocked."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = MINIMAL_OPENAPI
    mock_response.content = MINIMAL_OPENAPI.encode()
    mock_response.headers = {"content-length": str(len(MINIMAL_OPENAPI.encode()))}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=mock_client):
        handler = ImportHandler(integration_context)
        result = await handler.execute(
            job_id=str(uuid.uuid4()),
            session=None,
            payload={
                "sources": [
                    {
                        "type": "url",
                        "url": "https://api.example.com/openapi.json",
                        "vendor": "url-vendor",
                        "api_name": "url-api",
                        "version": "3.0.0",
                    }
                ]
            },
            created_by="usr_test",
        )

    revisions = result.body["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["api"]["vendor"] == "url-vendor"
    assert revisions[0]["state"] == "draft"

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1
