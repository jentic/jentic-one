"""Integration tests for the ImportHandler end-to-end against real databases."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.events import Event
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.exc import IngestJobError
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.services.catalog.service import CatalogEntryView, CatalogService
from jentic_one.registry.services.errors import (
    HostOwnedByOtherVendorError,
    OverlayStateConflictError,
)
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.overlay_service import OverlayService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_IMPORT_SVC = "jentic_one.registry.services.import_service"

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
        job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
    )
    first_revision_id = first.body["revisions"][0]["revision_id"]

    # Re-import the exact same content — must succeed, not raise.
    second = await handler.execute(
        job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
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
        db=None,
        payload={"sources": [good_source, bad_source]},
        created_by="usr_test",
    )

    async with registry_db.session() as session:
        rows = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(rows) == 1  # the good source's draft survived the sibling failure

    # Re-importing the same good content must not collide.
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
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
        job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
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
            job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
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
        db=None,
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
        db=None,
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
            db=None,
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
        db=None,
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
        base_rev_pre = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        base_digest = base_rev_pre.spec_digest
    assert base_digest is not None

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
        db=None,
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
                    "overlay_base_digest": base_digest,
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
        # A2: the base spec's digest is persisted on the materialized overlay revision
        # so the Flow-3 sweep can diff upstream against the overlay's base.
        assert new_rev.overlay_base_digest == base_digest

        spec_file = (
            await session.execute(select(SpecFile).where(SpecFile.revision_id == new_revision_id))
        ).scalar_one()
        assert spec_file.content["servers"] == [{"url": "https://new.example.com"}]

        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.confirmed_revision_id == new_revision_id
        assert overlay_row.superseded_revision_id == base_revision_id


async def test_overlay_rollback_restores_superseded_revision(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A5b: rollback un-confirms a live overlay and restores the revision it superseded.

    Sets up the committed post-materialize state (base archived, overlay revision current
    and CONFIRMED with confirmed_/superseded_revision_id linked), then rolls back via
    OverlayService. The served revision must revert to the base (un-archived, current),
    the overlay revision must be archived, and the overlay must be DEPRECATED.
    """
    handler = ImportHandler(integration_context)
    base_revision_id = await _import_base(
        handler, vendor="rb-vendor", name="rb-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "rb-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    # Materialize the overlay (the job the confirm would enqueue).
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "RB", "version": "1.0.0"},
                            "servers": [{"url": "https://new.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "rb-vendor",
                    "api_name": "rb-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )
    overlay_revision_id = uuid.UUID(result.body["revisions"][0]["revision_id"])

    # Flip the overlay to CONFIRMED (the confirm service does this via CAS; the raw
    # materialize job only links the revision). Rollback requires CONFIRMED.
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()

    identity = Identity(sub="usr_operator", email="op@test.local", permissions=["overlays:confirm"])
    await OverlayService(integration_context).rollback(
        "rb-vendor", "rb-api", "1.0.0", overlay_id, identity=identity
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        # Served revision reverted to the base.
        assert api.current_revision_id == base_revision_id

        base_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        assert base_rev.state == "imported"  # restored (un-archived)
        assert base_rev.archived_at is None

        overlay_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == overlay_revision_id))
        ).scalar_one()
        assert overlay_rev.state == "archived"  # the overlay's revision is retired

        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.status == "deprecated"
        assert overlay_row.deprecated_at is not None
        # The cause is persisted so clients can durably label the event "rolled back"
        # instead of re-deriving the verb from the (moving) current revision pointer.
        assert overlay_row.deprecated_reason == "rollback"


async def test_overlay_rollback_restores_published_base_as_published(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A5b + #939: a manually-PUBLISHED base is restored to PUBLISHED, not IMPORTED.

    An overlay's base can be a manually-promoted PUBLISHED revision (origin-less), not
    only an imported one. Rollback must restore it to its true prior state. Mirrors
    test_overlay_rollback_restores_superseded_revision but marks the base PUBLISHED +
    origin-less (as promote() would leave it) before materializing.
    """
    handler = ImportHandler(integration_context)
    base_revision_id = await _import_base(
        handler, vendor="rbp-vendor", name="rbp-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        # Make the base look like a manually-promoted PUBLISHED revision: PUBLISHED
        # state with no origin (create_draft → promote never sets origin).
        await session.execute(
            update(ApiRevision)
            .where(ApiRevision.id == base_revision_id)
            .values(state="published", origin=None)
        )
        api = (await session.execute(select(Api).where(Api.vendor == "rbp-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "RBP", "version": "1.0.0"},
                            "servers": [{"url": "https://new.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "rbp-vendor",
                    "api_name": "rbp-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )

    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()

    # Precondition the assertion depends on: materialization must have actually archived
    # the base. Without this, the final state=="published" check would also pass if the
    # base were never archived/restored at all (it started PUBLISHED) — masking a broken
    # archive→restore round-trip. See #939.
    async with registry_db.session() as session:
        base_rev_mid = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        assert base_rev_mid.state == "archived"

    identity = Identity(sub="usr_operator", email="op@test.local", permissions=["overlays:confirm"])
    await OverlayService(integration_context).rollback(
        "rbp-vendor", "rbp-api", "1.0.0", overlay_id, identity=identity
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == base_revision_id
        base_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        # The label is preserved through the archive→restore round-trip (#939): a
        # PUBLISHED base comes back PUBLISHED, not silently demoted to IMPORTED.
        assert base_rev.state == "published"
        assert base_rev.archived_at is None


async def test_overlay_rollback_conflict_when_not_live(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """Rollback of an overlay that isn't the currently-served revision is a 409 no-op.

    A CONFIRMED overlay whose confirmed_revision_id is not the API's current revision
    (already superseded/rolled back) must raise a state conflict and change nothing.
    """
    handler = ImportHandler(integration_context)
    base_revision_id = await _import_base(
        handler, vendor="rb2-vendor", name="rb2-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "rb2-vendor"))).scalar_one()
        api_id = api.id
        # A CONFIRMED overlay that points at some *other* (not current) revision.
        overlay = Overlay(
            api_id=api_id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=uuid.uuid4(),
            superseded_revision_id=base_revision_id,
            created_by="usr_test",
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    identity = Identity(sub="usr_operator", email="op@test.local", permissions=["overlays:confirm"])
    with pytest.raises(OverlayStateConflictError):
        await OverlayService(integration_context).rollback(
            "rb2-vendor", "rb2-api", "1.0.0", overlay_id, identity=identity
        )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        # Unchanged: base is still current, nothing rolled.
        assert api.current_revision_id == base_revision_id


async def test_overlay_rollback_refused_when_another_vendor_now_serves_the_host(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """Rollback makes the superseded revision live again, so the host rule applies.

    After materialize moves the API to ``new.example.com``, the base's
    ``old.example.com`` is no longer served by it, so another vendor may go live
    there. Restoring the base would then put two vendors on one host: the rollback
    is refused with ``HostOwnedByOtherVendorError`` and changes nothing.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="rb3-vendor", name="rb3-api", version="1.0.0", origin="catalog"
    )
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "rb3-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    overlaid = json.loads(_BASE_WITH_SERVERS)
    overlaid["servers"] = [{"url": "https://new.example.com"}]
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(overlaid),
                    "filename": "openapi.json",
                    "vendor": "rb3-vendor",
                    "api_name": "rb3-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )
    overlay_revision_id = uuid.UUID(result.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()

    # old.example.com is free now; another vendor goes live on it.
    await _import_base(
        handler, vendor="other-vendor", name="other-api", version="1.0.0", origin="catalog"
    )

    identity = Identity(sub="usr_operator", email="op@test.local", permissions=["overlays:confirm"])
    with pytest.raises(HostOwnedByOtherVendorError, match=r"old\.example\.com"):
        await OverlayService(integration_context).rollback(
            "rb3-vendor", "rb3-api", "1.0.0", overlay_id, identity=identity
        )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == overlay_revision_id
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.status == "confirmed"


async def _rematerialize_via_service(
    handler: ImportHandler,
    integration_context: Context,
    *,
    vendor: str,
    name: str,
    version: str,
    overlay_id: str,
    document: dict[str, Any],
    identity: Identity,
) -> uuid.UUID:
    """Drive OverlayService.update's re-materialize and run the enqueued job.

    Integration tests have no worker polling admin_db, so capture the job payload the
    service enqueues (the unit under test builds it — clean base + overlaid doc) and run
    it through the handler here, returning the new revision id. This exercises the whole
    D1 path: service authorize/guard/apply → enqueue → worker ingest/relink.
    """
    captured: dict[str, Any] = {}

    async def _capture(self: OverlayService, *args: Any, **kwargs: Any) -> None:
        captured["overlaid_spec"] = kwargs["overlaid_spec"]
        captured["base_source_url"] = kwargs["base_source_url"]
        captured["base_digest"] = kwargs["base_digest"]

    with patch.object(OverlayService, "_enqueue_materialize_job", _capture):
        await OverlayService(integration_context).update(
            vendor, name, version, overlay_id, document=document, identity=identity
        )

    assert "overlaid_spec" in captured, "re-materialize did not enqueue a job"
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(captured["overlaid_spec"]),
                    "filename": "openapi.json",
                    "vendor": vendor,
                    "api_name": name,
                    "version": version,
                    "origin": "overlay",
                    "source_url": captured["base_source_url"],
                    "overlay_base_digest": captured["base_digest"],
                    "overlay_id": overlay_id,
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by=identity.sub,
    )
    return uuid.UUID(result.body["revisions"][0]["revision_id"])


async def test_rematerialize_on_edit_reapplies_over_clean_base_and_keeps_chain(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """D1: editing a live confirmed overlay re-materializes over the ORIGINAL clean base.

    Sets up the committed post-materialize state (base archived, overlay revision current
    and CONFIRMED, superseded/confirmed linked). Editing the overlay document via
    ``OverlayService.update`` must:

    - re-apply the *edited* document over the pre-overlay clean base (not the overlay's own
      output — no double-apply),
    - promote a new overlay revision to current and archive the prior overlay revision
      (the full chain is retained),
    - keep ``superseded_revision_id`` pointing at the clean base (so a later rollback still
      restores upstream, not an orphaned overlay output),
    - relink ``confirmed_revision_id`` to the new revision.
    """
    handler = ImportHandler(integration_context)
    base_revision_id = await _import_base(
        handler, vendor="rem-vendor", name="rem-api", version="1.0.0", origin="catalog"
    )
    async with registry_db.session() as session:
        base_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        base_digest = base_rev.spec_digest
        api = (await session.execute(select(Api).where(Api.vendor == "rem-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    # First materialize (the confirm path): overlay v1 becomes current over the clean base.
    v1_id = await _rematerialize_via_service_first(
        handler, api_id, overlay_id, base_digest, registry_db
    )

    identity = Identity(sub="usr_operator", email="op@test.local", permissions=["overlays:confirm"])

    # Edit the overlay: rewrite servers to a *different* URL than the first materialize.
    edited_doc = {
        "overlay": "1.0.0",
        "actions": [
            {"target": "$.servers", "remove": True},
            {"target": "$", "update": {"servers": [{"url": "https://edited.example.com"}]}},
        ],
    }
    v2_id = await _rematerialize_via_service(
        handler,
        integration_context,
        vendor="rem-vendor",
        name="rem-api",
        version="1.0.0",
        overlay_id=overlay_id,
        document=edited_doc,
        identity=identity,
    )
    assert v2_id not in (base_revision_id, v1_id)

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == v2_id  # new revision is served

        v2_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == v2_id))
        ).scalar_one()
        # Applied over the CLEAN base (its overlay_base_digest is the base's), not v1.
        assert v2_rev.overlay_base_digest == base_digest

        v1_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == v1_id))
        ).scalar_one()
        assert v1_rev.state == "archived"  # prior overlay revision retained, archived
        base_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == base_revision_id))
        ).scalar_one()
        assert base_rev.state == "archived"  # clean base still archived

        spec_file = (
            await session.execute(select(SpecFile).where(SpecFile.revision_id == v2_id))
        ).scalar_one()
        # The EDITED servers are served, proving the edit re-applied over the clean base.
        assert spec_file.content["servers"] == [{"url": "https://edited.example.com"}]

        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.confirmed_revision_id == v2_id
        # Superseded pointer stays the CLEAN base across the edit (not moved onto v1).
        assert overlay_row.superseded_revision_id == base_revision_id

    # A rollback after the edit restores the clean base (not the orphaned v1 output).
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()
    await OverlayService(integration_context).rollback(
        "rem-vendor", "rem-api", "1.0.0", overlay_id, identity=identity
    )
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == base_revision_id


async def _rematerialize_via_service_first(
    handler: ImportHandler,
    api_id: uuid.UUID,
    overlay_id: str,
    base_digest: str | None,
    registry_db: DatabaseSession,
) -> uuid.UUID:
    """Run the initial materialize job (confirm path) and flip the overlay to CONFIRMED."""
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "Overlay Base", "version": "1.0.0"},
                            "servers": [{"url": "https://new.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "rem-vendor",
                    "api_name": "rem-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                    "overlay_base_digest": base_digest,
                    "overlay_id": overlay_id,
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )
    v1_id = uuid.UUID(result.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()
    return v1_id


async def test_stacked_overlay_confirm_captures_prior_overlay_as_superseded(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """Confirming overlay B over live overlay A captures A's revision as B's superseded target.

    Regression guard for the D1 superseded-capture skip: that skip must fire only for a
    *re-materialize of the same overlay*, NOT for a *different* overlay stacked on a live
    overlay's output. If the skip over-fired here, overlay B would get a NULL
    superseded_revision_id and could never be rolled back or re-materialized. Confirm B is
    linked to A's revision and can roll back to it.
    """
    handler = ImportHandler(integration_context)
    base_revision_id = await _import_base(
        handler, vendor="stk-vendor", name="stk-api", version="1.0.0", origin="catalog"
    )
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "stk-vendor"))).scalar_one()
        api_id = api.id
        overlay_a = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay_a)
        await session.commit()
        overlay_a_id = overlay_a.id

    def _overlaid(url: str) -> str:
        return json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Overlay Base", "version": "1.0.0"},
                "servers": [{"url": url}],
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

    async def _materialize(overlay_id: str, url: str) -> uuid.UUID:
        result = await handler.execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={
                "sources": [
                    {
                        "type": "inline",
                        "content": _overlaid(url),
                        "filename": "openapi.json",
                        "vendor": "stk-vendor",
                        "api_name": "stk-api",
                        "version": "1.0.0",
                        "origin": "overlay",
                        "source_url": "https://catalog.example.com/base.json",
                        "overlay_id": overlay_id,
                    }
                ],
                "overlay_id": overlay_id,
            },
            created_by="usr_test",
        )
        rev = uuid.UUID(result.body["revisions"][0]["revision_id"])
        async with registry_db.session() as s:
            await s.execute(
                update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
            )
            await s.commit()
        return rev

    # Confirm A over the clean base → A's revision is current, superseded = clean base.
    rev_a = await _materialize(overlay_a_id, "https://a.example.com")
    async with registry_db.session() as session:
        a_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_a_id))
        ).scalar_one()
        assert a_row.superseded_revision_id == base_revision_id

    # A different overlay B, stacked over A's live output.
    async with registry_db.session() as session:
        overlay_b = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay_b)
        await session.commit()
        overlay_b_id = overlay_b.id

    rev_b = await _materialize(overlay_b_id, "https://b.example.com")
    assert rev_b not in (base_revision_id, rev_a)

    async with registry_db.session() as session:
        b_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_b_id))
        ).scalar_one()
        # THE REGRESSION GUARD: B captured A's revision as its superseded target (not NULL,
        # not the clean base) — the D1 skip did not over-fire for a different overlay.
        assert b_row.superseded_revision_id == rev_a
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == rev_b

    # B can be rolled back deterministically, restoring A's revision (not the clean base).
    identity = Identity(sub="usr_operator", email="op@test.local", permissions=["overlays:confirm"])
    await OverlayService(integration_context).rollback(
        "stk-vendor", "stk-api", "1.0.0", overlay_b_id, identity=identity
    )
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == rev_a


async def test_authorized_supersede_reimport_deprecates_overlay(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A4b: an authorized catalog re-import over a live confirmed overlay supersedes it.

    Sets up a live confirmed overlay (its materialized revision is current), then runs
    the catalog re-import job the scope-checked enqueue path would produce — carrying
    ``supersede_active`` on the source and ``supersede_overlay_id`` on the payload. The
    fresh catalog revision must become current (the overlay revision archived) and the
    overlay must be auto-DEPRECATED in the same job.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="sup-vendor", name="sup-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "sup-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    # Materialize + confirm the overlay so it is the live served revision.
    result = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "SUP", "version": "1.0.0"},
                            "servers": [{"url": "https://overlaid.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "sup-vendor",
                    "api_name": "sup-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )
    overlay_revision_id = uuid.UUID(result.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()

    # The fresh upstream content the catalog re-import adopts. Reused verbatim by the retry's
    # mocked fetch below so the retry re-ingests identical bytes → DuplicateRevisionError.
    fresh_upstream = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "SUP", "version": "1.0.0"},
            "servers": [{"url": "https://upstream-fresh.example.com"}],
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

    # The scope-checked enqueue path stamps supersede_active + supersede_overlay_id.
    reimport = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": fresh_upstream,
                    "filename": "openapi.json",
                    "vendor": "sup-vendor",
                    "api_name": "sup-api",
                    "version": "1.0.0",
                    "origin": "catalog",
                    "source_url": "https://catalog.example.com/base.json",
                    "supersede_active": "true",
                }
            ],
            "supersede_overlay_id": overlay_id,
        },
        created_by="usr_operator",
    )
    new_revision_id = uuid.UUID(reimport.body["revisions"][0]["revision_id"])
    assert new_revision_id != overlay_revision_id
    # Same API row and the overlay's revision was archived: the #1078 guard lets it through.
    assert "supersede_skipped" not in reimport.body["revisions"][0]

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        # The fresh catalog revision is now served.
        assert api.current_revision_id == new_revision_id

        overlay_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == overlay_revision_id))
        ).scalar_one()
        assert overlay_rev.state == "archived"

        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.status == "deprecated"
        assert overlay_row.deprecated_at is not None
        assert overlay_row.deprecated_reason == "superseded_by_reimport"

    # --- Simulate a crash between the committed re-ingest and the separate-transaction
    # deprecate: reset the overlay to CONFIRMED (as if the deprecate never committed) and
    # re-run the *identical* supersede job. The re-ingest now hits DuplicateRevisionError
    # (fresh revision already exists + is current), so there are no new revisions and a
    # failure — the retry must RECOVER (not dead-letter): served spec is already the fresh
    # upstream, so _recover_supersede completes the job and re-deprecates the overlay.
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay).where(Overlay.id == overlay_id).values(status="confirmed")
        )
        await session.commit()

    # Faithfully mirror the PRODUCTION supersede source: a ``type:"url"`` payload with NO
    # version key (CatalogService._to_import_source builds exactly this — the version isn't
    # known until the spec is fetched). Mock the HTTP fetch to return the *identical* fresh
    # upstream bytes so the re-ingest actually runs through load_specification/Ingestor and
    # raises DuplicateRevisionError — the real trigger _recover_supersede handles. This proves
    # _recover_supersede resolves identity by url (current_revision_for_source_url), not by a
    # vendor/name/version triple the real path never carries.
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = fresh_upstream
    mock_response.content = fresh_upstream.encode()
    mock_response.headers = {"content-length": str(len(fresh_upstream.encode()))}
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=mock_client):
        retry = await handler.execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={
                "sources": [
                    {
                        "type": "url",
                        "url": "https://catalog.example.com/base.json",
                        "vendor": "sup-vendor",
                        "api_name": "sup-api",
                        "origin": "catalog",
                        "source_url": "https://catalog.example.com/base.json",
                        "supersede_active": "true",
                    }
                ],
                "supersede_overlay_id": overlay_id,
            },
            created_by="usr_operator",
        )
    # The job completed (did not raise / dead-letter) with no new revision.
    assert retry.body["revisions"] == []

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        # Served spec unchanged: still the fresh upstream from the first pass.
        assert api.current_revision_id == new_revision_id
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        # Recovery re-ran the idempotent CAS deprecate.
        assert overlay_row.status == "deprecated"


async def test_supersede_deprecates_overlay_backing_archived_revision(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#940: a concurrent confirm can make a *different* overlay live after enqueue.

    The A4b authorization gate resolves the live overlay in a lock-free read and stamps
    its id as ``supersede_overlay_id``. If a concurrent confirm makes overlay **B** live
    (its ``confirmed_revision_id`` is the API's current revision) before the worker runs,
    the worker archives whatever is current *now* — B's revision — but the stale
    ``supersede_overlay_id`` still points at the previously-live overlay **A**. The worker
    must re-resolve and deprecate **B** (the overlay that actually backed the archived
    revision), leaving A untouched.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="race-vendor", name="race-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "race-vendor"))).scalar_one()
        api_id = api.id
        # Overlay A: the enqueue-time target. CONFIRMED but *not* backing the current
        # revision — it points at some other (superseded) revision, exactly like a
        # prior-live overlay that a concurrent confirm stacked over.
        overlay_a = Overlay(
            api_id=api_id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=uuid.uuid4(),
            created_by="usr_author_a",
        )
        # Overlay B: the concurrently-confirmed one whose materialized revision will be
        # current when the worker runs.
        overlay_b = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_author_b"
        )
        session.add_all([overlay_a, overlay_b])
        await session.commit()
        overlay_a_id = overlay_a.id
        overlay_b_id = overlay_b.id

    # Materialize overlay B so its revision is the live served revision, then flip it
    # CONFIRMED and link confirmed_revision_id to the served revision (what confirm does).
    materialize = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "RACE", "version": "1.0.0"},
                            "servers": [{"url": "https://overlaid-b.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "race-vendor",
                    "api_name": "race-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_b_id,
        },
        created_by="usr_test",
    )
    b_revision_id = uuid.UUID(materialize.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_b_id)
            .values(status="confirmed", confirmed_revision_id=b_revision_id)
        )
        await session.commit()

    # The scope-checked enqueue path stamped the STALE overlay A (it was live when the
    # gate ran); by worker time B is the live/current one.
    fresh_upstream = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "RACE", "version": "1.0.0"},
            "servers": [{"url": "https://upstream-fresh.example.com"}],
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
    reimport = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": fresh_upstream,
                    "filename": "openapi.json",
                    "vendor": "race-vendor",
                    "api_name": "race-api",
                    "version": "1.0.0",
                    "origin": "catalog",
                    "source_url": "https://catalog.example.com/base.json",
                    "supersede_active": "true",
                }
            ],
            "supersede_overlay_id": overlay_a_id,
        },
        created_by="usr_operator",
    )
    new_revision_id = uuid.UUID(reimport.body["revisions"][0]["revision_id"])

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == new_revision_id
        b_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == b_revision_id))
        ).scalar_one()
        assert b_rev.state == "archived"

        overlay_a_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_a_id))
        ).scalar_one()
        overlay_b_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_b_id))
        ).scalar_one()
        # B backed the archived revision → B is deprecated; A (the stale enqueue target)
        # is left untouched.
        assert overlay_b_row.status == "deprecated"
        assert overlay_b_row.deprecated_reason == "superseded_by_reimport"
        assert overlay_a_row.status == "confirmed"
        assert overlay_a_row.deprecated_at is None


async def test_supersede_deprecates_target_when_no_race(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#940 regression: the common no-race case still deprecates the stamped overlay.

    When the enqueue-time ``supersede_overlay_id`` *is* the overlay backing the current
    revision (no concurrent confirm), re-resolution returns that same overlay and it is
    deprecated — no behaviour change versus before the fix.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="norace-vendor", name="norace-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "norace-vendor"))).scalar_one()
        api_id = api.id
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    materialize = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "NORACE", "version": "1.0.0"},
                            "servers": [{"url": "https://overlaid.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "norace-vendor",
                    "api_name": "norace-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_id,
        },
        created_by="usr_test",
    )
    overlay_revision_id = uuid.UUID(materialize.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_id)
            .values(status="confirmed", confirmed_revision_id=overlay_revision_id)
        )
        await session.commit()

    fresh_upstream = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "NORACE", "version": "1.0.0"},
            "servers": [{"url": "https://upstream-fresh.example.com"}],
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
    await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": fresh_upstream,
                    "filename": "openapi.json",
                    "vendor": "norace-vendor",
                    "api_name": "norace-api",
                    "version": "1.0.0",
                    "origin": "catalog",
                    "source_url": "https://catalog.example.com/base.json",
                    "supersede_active": "true",
                }
            ],
            "supersede_overlay_id": overlay_id,
        },
        created_by="usr_operator",
    )

    async with registry_db.session() as session:
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.status == "deprecated"
        assert overlay_row.deprecated_reason == "superseded_by_reimport"


async def test_supersede_recovers_overlay_in_lazy_link_window(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#940 lazy-link: the live overlay is CONFIRMED but not yet linked (NULL link).

    The confirm path makes an overlay's revision current *before* it stamps
    ``confirmed_revision_id`` (separate best-effort txn). In that window the strict
    revision-keyed re-resolution misses, so the worker must recover the live overlay via
    the single NULL-linked CONFIRMED overlay for the API rather than falling back to the
    stale enqueue-time id.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="lazy-vendor", name="lazy-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "lazy-vendor"))).scalar_one()
        api_id = api.id
        # Stale enqueue target A: CONFIRMED, linked to some other (superseded) revision.
        overlay_a = Overlay(
            api_id=api_id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=uuid.uuid4(),
            created_by="usr_author_a",
        )
        overlay_b = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_author_b"
        )
        session.add_all([overlay_a, overlay_b])
        await session.commit()
        overlay_a_id = overlay_a.id
        overlay_b_id = overlay_b.id

    materialize = await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(
                        {
                            "openapi": "3.1.0",
                            "info": {"title": "LAZY", "version": "1.0.0"},
                            "servers": [{"url": "https://overlaid-b.example.com"}],
                            "paths": {
                                "/items": {
                                    "get": {
                                        "operationId": "listItems",
                                        "responses": {"200": {"description": "OK"}},
                                    }
                                }
                            },
                        }
                    ),
                    "filename": "openapi.json",
                    "vendor": "lazy-vendor",
                    "api_name": "lazy-api",
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": "https://catalog.example.com/base.json",
                }
            ],
            "overlay_id": overlay_b_id,
        },
        created_by="usr_test",
    )
    b_revision_id = uuid.UUID(materialize.body["revisions"][0]["revision_id"])
    # Lazy-link window: B is CONFIRMED and its revision is current, but confirmed_revision_id
    # is still NULL (the link txn hasn't landed / failed).
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_b_id)
            .values(status="confirmed", confirmed_revision_id=None)
        )
        await session.commit()

    fresh_upstream = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "LAZY", "version": "1.0.0"},
            "servers": [{"url": "https://upstream-fresh.example.com"}],
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
    await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": fresh_upstream,
                    "filename": "openapi.json",
                    "vendor": "lazy-vendor",
                    "api_name": "lazy-api",
                    "version": "1.0.0",
                    "origin": "catalog",
                    "source_url": "https://catalog.example.com/base.json",
                    "supersede_active": "true",
                }
            ],
            "supersede_overlay_id": overlay_a_id,
        },
        created_by="usr_operator",
    )

    async with registry_db.session() as session:
        b_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == b_revision_id))
        ).scalar_one()
        assert b_rev.state == "archived"
        overlay_a_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_a_id))
        ).scalar_one()
        overlay_b_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_b_id))
        ).scalar_one()
        # Recovered via the single NULL-linked CONFIRMED overlay → B deprecated, A untouched.
        assert overlay_b_row.status == "deprecated"
        assert overlay_b_row.deprecated_reason == "superseded_by_reimport"
        assert overlay_a_row.status == "confirmed"
        assert overlay_a_row.deprecated_at is None


async def test_supersede_falls_back_to_enqueue_id_when_no_backing_overlay(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#940 fallback: a catalog-origin base (no overlay backs the archived revision).

    When the archived revision was not overlay-origin, there is no overlay to re-resolve, so
    the worker falls back to the enqueue-time id and deprecates it. This exercises the
    strict-miss → not-overlay-origin → fallback branch.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="fb-vendor", name="fb-api", version="1.0.0", origin="catalog"
    )

    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "fb-vendor"))).scalar_one()
        api_id = api.id
        # A CONFIRMED overlay stamped as the enqueue target, but the current revision is the
        # catalog base (overlay never materialized), so nothing overlay-origin backs it.
        overlay = Overlay(
            api_id=api_id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=uuid.uuid4(),
            created_by="usr_author",
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    fresh_upstream = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "FB", "version": "1.0.0"},
            "servers": [{"url": "https://upstream-fresh.example.com"}],
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
    await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": fresh_upstream,
                    "filename": "openapi.json",
                    "vendor": "fb-vendor",
                    "api_name": "fb-api",
                    "version": "1.0.0",
                    "origin": "catalog",
                    "source_url": "https://catalog.example.com/base.json",
                    "supersede_active": "true",
                }
            ],
            "supersede_overlay_id": overlay_id,
        },
        created_by="usr_operator",
    )

    async with registry_db.session() as session:
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        # Fallback to the enqueue-time id deprecated it (no regression vs pre-#940).
        assert overlay_row.status == "deprecated"
        assert overlay_row.deprecated_reason == "superseded_by_reimport"


def _spec(title: str, server: str) -> str:
    """A one-operation OpenAPI document served from ``server``."""
    return json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": title, "version": "1.0.0"},
            "servers": [{"url": server}],
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


_CATALOG_URL = "https://catalog.example.com/base.json"
_FRESH_UPSTREAM = _spec("PostHog", "https://upstream-fresh.example.com")


async def _run_job(
    handler: ImportHandler, payload: dict[str, Any], *, created_by: str = "usr_test"
) -> Any:
    """Run one import job through ``ImportHandler.execute``.

    The worker database argument (unused by the import handler) is passed positionally so
    the call does not depend on that parameter's name.
    """
    return await handler.execute(str(uuid.uuid4()), None, payload=payload, created_by=created_by)


def _supersede_payload(overlay_id: str, *, vendor: str, name: str) -> dict[str, Any]:
    """The authorized A4b catalog re-import the scope-checked enqueue path produces."""
    return {
        "sources": [
            {
                "type": "inline",
                "content": _FRESH_UPSTREAM,
                "filename": "openapi.json",
                "vendor": vendor,
                "api_name": name,
                "version": "1.0.0",
                "origin": "catalog",
                "source_url": _CATALOG_URL,
                "supersede_active": "true",
            }
        ],
        "supersede_overlay_id": overlay_id,
    }


def _supersede_retry_payload(overlay_id: str, *, vendor: str, name: str) -> dict[str, Any]:
    """The production-shaped retry of a supersede: a ``type:"url"`` source, no version."""
    return {
        "sources": [
            {
                "type": "url",
                "url": _CATALOG_URL,
                "vendor": vendor,
                "api_name": name,
                "origin": "catalog",
                "source_url": _CATALOG_URL,
                "supersede_active": "true",
            }
        ],
        "supersede_overlay_id": overlay_id,
    }


async def _api_id(registry_db: DatabaseSession, name: str) -> uuid.UUID:
    async with registry_db.session() as session:
        return (await session.execute(select(Api.id).where(Api.name == name))).scalar_one()


async def _confirmed_overlay(
    handler: ImportHandler,
    registry_db: DatabaseSession,
    *,
    vendor: str,
    name: str,
    linked: bool = True,
) -> tuple[str, uuid.UUID]:
    """Materialize a confirmed overlay on ``vendor/name``; returns (overlay id, its revision).

    ``linked=False`` leaves ``confirmed_revision_id`` NULL (the lazy-link window).
    """
    api_id = await _api_id(registry_db, name)
    async with registry_db.session() as session:
        overlay = Overlay(
            api_id=api_id, document=_OVERLAY_DOC, status="pending", created_by="usr_test"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id
    materialize = await _run_job(
        handler,
        {
            "sources": [
                {
                    "type": "inline",
                    "content": _spec("PostHog", "https://overlaid.example.com"),
                    "filename": "openapi.json",
                    "vendor": vendor,
                    "api_name": name,
                    "version": "1.0.0",
                    "origin": "overlay",
                    "source_url": _CATALOG_URL,
                }
            ],
            "overlay_id": overlay_id,
        },
    )
    overlay_revision_id = uuid.UUID(materialize.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_id)
            .values(
                status="confirmed",
                confirmed_revision_id=overlay_revision_id if linked else None,
            )
        )
        await session.commit()
    return overlay_id, overlay_revision_id


async def _overlay_deprecated_events(ctx: Context, overlay_id: str) -> list[Event]:
    """``overlay.deprecated`` events in the admin DB for ``overlay_id``."""
    async with ctx.admin_db.session() as session:
        rows = (
            (await session.execute(select(Event).where(Event.type == EventType.OVERLAY_DEPRECATED)))
            .scalars()
            .all()
        )
    return [row for row in rows if (row.data or {}).get("overlay_id") == overlay_id]


async def _assert_overlay_still_confirmed(
    ctx: Context,
    registry_db: DatabaseSession,
    overlay_id: str,
    *,
    api_id: uuid.UUID,
    overlay_revision_id: uuid.UUID,
) -> None:
    """The overlay's revision is still served on its own row and the overlay reads CONFIRMED."""
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == overlay_revision_id
        overlay_rev = (
            await session.execute(select(ApiRevision).where(ApiRevision.id == overlay_revision_id))
        ).scalar_one()
        assert overlay_rev.state == "imported"
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.status == "confirmed"
        assert overlay_row.deprecated_at is None
        assert overlay_row.deprecated_reason is None
    assert await _overlay_deprecated_events(ctx, overlay_id) == []


@pytest.mark.parametrize("overlay_linked", [True, False], ids=["linked", "lazy_link"])
@pytest.mark.parametrize("clean_row_has_current_revision", [False, True])
async def test_supersede_onto_different_api_row_leaves_overlay_confirmed(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
    clean_row_has_current_revision: bool,
    overlay_linked: bool,
) -> None:
    """#1078: a supersede re-import that lands on a *different* API row deprecates nothing.

    Transition-window shape: a confirmed overlay serves a vendor-doubled row
    (``posthog-com/posthog-com-posthog-api``) and the authorized A4b re-import resolves
    the clean identity (``posthog-com/posthog-api``) — a different ``Api`` row. The ingest
    archives only the clean row's active revision (if any), so the overlay's revision is
    still served on the doubled row. The worker must leave the overlay CONFIRMED, emit no
    ``overlay.deprecated`` event, and record the skip on the job result. Parametrized over
    the resolver's two branches (the clean row has no current revision → nothing archived;
    or has one → the archived revision has no backing overlay) and over the overlay's link:
    with ``confirmed_revision_id`` NULL (lazy-link window) only the API check can tell the
    overlay apart, since there is no linked revision to find still served.
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler,
        vendor="posthog-com",
        name="posthog-com-posthog-api",
        version="1.0.0",
        origin="catalog",
    )
    if clean_row_has_current_revision:
        await _import_base(
            handler, vendor="posthog-com", name="posthog-api", version="1.0.0", origin="catalog"
        )
    doubled_api_id = await _api_id(registry_db, "posthog-com-posthog-api")
    overlay_id, overlay_revision_id = await _confirmed_overlay(
        handler,
        registry_db,
        vendor="posthog-com",
        name="posthog-com-posthog-api",
        linked=overlay_linked,
    )

    with patch(f"{_IMPORT_SVC}.record_overlay_supersede_skipped") as skipped_metric:
        reimport = await _run_job(
            handler,
            _supersede_payload(overlay_id, vendor="posthog-com", name="posthog-api"),
            created_by="usr_operator",
        )
    [revision] = reimport.body["revisions"]
    assert revision["api"]["name"] == "posthog-api"
    assert revision["supersede_skipped"] == "api_mismatch"
    skipped_metric.assert_called_once_with("api_mismatch")
    await _assert_overlay_still_confirmed(
        integration_context,
        registry_db,
        overlay_id,
        api_id=doubled_api_id,
        overlay_revision_id=overlay_revision_id,
    )


async def _doubled_and_clean_rows(
    handler: ImportHandler, registry_db: DatabaseSession
) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID]:
    """A clean row and a vendor-doubled row both served from ``_CATALOG_URL``.

    The confirmed overlay lives on the doubled row. The clean row is made the *oldest*, so
    an unscoped oldest-first ``source_url`` lookup resolves the clean row — the row order
    under which an unscoped recovery lookup picks the wrong API. Returns
    ``(doubled_api_id, clean_api_id, overlay_id, overlay_revision_id)``.
    """
    await _import_base(
        handler, vendor="posthog-com", name="posthog-api", version="1.0.0", origin="catalog"
    )
    await _import_base(
        handler,
        vendor="posthog-com",
        name="posthog-com-posthog-api",
        version="1.0.0",
        origin="catalog",
    )
    clean_api_id = await _api_id(registry_db, "posthog-api")
    doubled_api_id = await _api_id(registry_db, "posthog-com-posthog-api")
    async with registry_db.session() as session:
        await session.execute(
            update(Api)
            .where(Api.id == clean_api_id)
            .values(created_at=datetime(2020, 1, 1, tzinfo=UTC))
        )
        await session.commit()
    overlay_id, overlay_revision_id = await _confirmed_overlay(
        handler, registry_db, vendor="posthog-com", name="posthog-com-posthog-api"
    )
    return doubled_api_id, clean_api_id, overlay_id, overlay_revision_id


async def test_supersede_recovery_deprecates_when_fresh_upstream_landed_on_overlay_row(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#1078: duplicate-on-retry recovery keys on the overlay's own API row.

    The fresh upstream landed on the overlay's (doubled) row, but the clean row also serves
    a catalog revision from the same ``source_url`` and is the oldest, so an unscoped
    lookup resolves the clean row. Recovery must still find the supersede on the overlay's
    own row and finish the interrupted deprecate instead of skipping it as an API mismatch.
    """
    handler = ImportHandler(integration_context)
    doubled_api_id, clean_api_id, overlay_id, _ = await _doubled_and_clean_rows(
        handler, registry_db
    )
    first = await _run_job(
        handler,
        _supersede_payload(overlay_id, vendor="posthog-com", name="posthog-com-posthog-api"),
        created_by="usr_operator",
    )
    fresh_revision_id = uuid.UUID(first.body["revisions"][0]["revision_id"])
    # The unscoped lookup resolves the *clean* row here — the order the recovery must not
    # depend on.
    async with registry_db.session() as session:
        unscoped = await ApiRevisionRepository.current_revision_for_source_url(
            session, _CATALOG_URL
        )
    assert unscoped is not None and unscoped[0] == clean_api_id

    # Crash between the committed re-ingest and the separate-transaction deprecate.
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_id)
            .values(status="confirmed", deprecated_at=None, deprecated_reason=None)
        )
        await session.commit()
    with patch(
        "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
        return_value=_mock_spec_client(_FRESH_UPSTREAM),
    ):
        retry = await _run_job(
            handler,
            _supersede_retry_payload(
                overlay_id, vendor="posthog-com", name="posthog-com-posthog-api"
            ),
            created_by="usr_operator",
        )

    assert retry.body == {"revisions": []}
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == doubled_api_id))).scalar_one()
        assert api.current_revision_id == fresh_revision_id
        overlay_row = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert overlay_row.status == "deprecated"
        assert overlay_row.deprecated_reason == "superseded_by_reimport"


async def test_supersede_recovery_skips_when_fresh_upstream_landed_on_other_row(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#1078: recovery of a supersede that landed on a different row leaves the overlay.

    The fresh upstream landed on the clean row; the overlay's revision is still served on
    the doubled row. The retry completes (the re-import's effect is durable) but records
    ``supersede_skipped: api_mismatch`` and leaves the overlay CONFIRMED with no
    ``overlay.deprecated`` event.
    """
    handler = ImportHandler(integration_context)
    doubled_api_id, _, overlay_id, overlay_revision_id = await _doubled_and_clean_rows(
        handler, registry_db
    )
    first = await _run_job(
        handler,
        _supersede_payload(overlay_id, vendor="posthog-com", name="posthog-api"),
        created_by="usr_operator",
    )
    assert first.body["revisions"][0]["supersede_skipped"] == "api_mismatch"

    with (
        patch(
            "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
            return_value=_mock_spec_client(_FRESH_UPSTREAM),
        ),
        patch(f"{_IMPORT_SVC}.record_overlay_supersede_skipped") as skipped_metric,
    ):
        retry = await _run_job(
            handler,
            _supersede_retry_payload(overlay_id, vendor="posthog-com", name="posthog-api"),
            created_by="usr_operator",
        )

    assert retry.body == {"revisions": [], "supersede_skipped": "api_mismatch"}
    skipped_metric.assert_called_once_with("api_mismatch")
    await _assert_overlay_still_confirmed(
        integration_context,
        registry_db,
        overlay_id,
        api_id=doubled_api_id,
        overlay_revision_id=overlay_revision_id,
    )


@pytest.mark.parametrize(
    ("on_superseding_api", "linked_state", "expected_skip"),
    [
        # A different API row: skipped whatever the overlay's link says.
        (False, "imported", "api_mismatch"),
        (False, None, "api_mismatch"),
        # Same API row, but the overlay's revision is still the served one.
        (True, "imported", "revision_still_served"),
        (True, "published", "revision_still_served"),
        # Same API row and nothing of the overlay's is served: deprecate.
        (True, "archived", None),
        (True, "missing", None),
        (True, None, None),
    ],
    ids=[
        "mismatch_linked",
        "mismatch_lazy_link",
        "still_served_imported",
        "still_served_published",
        "archived",
        "missing_revision",
        "lazy_link",
    ],
)
async def test_deprecate_superseded_overlay_guard(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
    on_superseding_api: bool,
    linked_state: str | None,
    expected_skip: str | None,
) -> None:
    """#1078: the same-API guard on real rows — skip reason, overlay state, event, metric."""
    handler = ImportHandler(integration_context)
    async with registry_db.session() as session:
        overlay_api = Api(vendor="guard-vendor", name="guard-api", version="1.0.0")
        other_api = Api(vendor="guard-vendor", name="guard-other", version="1.0.0")
        session.add_all([overlay_api, other_api])
        await session.flush()
        confirmed_revision_id: uuid.UUID | None = None
        if linked_state == "missing":
            confirmed_revision_id = uuid.uuid4()
        elif linked_state is not None:
            revision = ApiRevision(
                api_id=overlay_api.id, state=linked_state, spec_digest="sha256:guard"
            )
            session.add(revision)
            await session.flush()
            confirmed_revision_id = revision.id
        overlay = Overlay(
            api_id=overlay_api.id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=confirmed_revision_id,
            created_by="usr_author",
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id
        superseding_api_id = overlay_api.id if on_superseding_api else other_api.id

    with (
        patch(f"{_IMPORT_SVC}.record_overlay_supersede_skipped") as skipped_metric,
        patch(f"{_IMPORT_SVC}.record_overlay_auto_deprecated") as deprecated_metric,
    ):
        skipped = await handler._deprecate_superseded_overlay(
            "job-guard",
            overlay_id,
            superseding_api_id=superseding_api_id,
            actor_id="usr_operator",
            actor_type="user",
        )

    assert skipped == expected_skip
    async with registry_db.session() as session:
        row = (await session.execute(select(Overlay).where(Overlay.id == overlay_id))).scalar_one()
    events = await _overlay_deprecated_events(integration_context, overlay_id)
    if expected_skip is None:
        assert row.status == "deprecated"
        assert row.deprecated_reason == "superseded_by_reimport"
        assert len(events) == 1
        deprecated_metric.assert_called_once_with()
        skipped_metric.assert_not_called()
    else:
        assert row.status == "confirmed"
        assert row.deprecated_at is None
        assert events == []
        deprecated_metric.assert_not_called()
        skipped_metric.assert_called_once_with(expected_skip)


async def test_deprecate_superseded_overlay_cas_rejects_relink_after_guard(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#1078: a relink that lands between the guard's read and the demote blocks the demote.

    The guard sees the overlay's revision archived; before the CAS runs, the overlay is
    relinked to a fresh, active revision (what a concurrent re-materialize commits). The
    CAS re-checks the linked revision and the served state, so the overlay stays
    CONFIRMED and no event is emitted.
    """
    handler = ImportHandler(integration_context)
    async with registry_db.session() as session:
        api = Api(vendor="cas-vendor", name="cas-api", version="1.0.0")
        session.add(api)
        await session.flush()
        archived = ApiRevision(api_id=api.id, state="archived", spec_digest="sha256:old")
        rematerialized = ApiRevision(api_id=api.id, state="imported", spec_digest="sha256:new")
        session.add_all([archived, rematerialized])
        await session.flush()
        overlay = Overlay(
            api_id=api.id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=archived.id,
            created_by="usr_author",
        )
        session.add(overlay)
        await session.commit()
        overlay_id, api_id, rematerialized_id = overlay.id, api.id, rematerialized.id

    guard = ImportHandler._supersede_skip_reason

    async def _guard_then_relink(
        session: Any, overlay: Overlay | None, superseding_api_id: uuid.UUID | None
    ) -> Any:
        reason = await guard(session, overlay, superseding_api_id)
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_id)
            .values(confirmed_revision_id=rematerialized_id)
            # Leave the guard's loaded overlay as it read it, like a separate transaction.
            .execution_options(synchronize_session=False)
        )
        return reason

    with patch.object(ImportHandler, "_supersede_skip_reason", staticmethod(_guard_then_relink)):
        skipped = await handler._deprecate_superseded_overlay(
            "job-cas",
            overlay_id,
            superseding_api_id=api_id,
            actor_id="usr_operator",
            actor_type="user",
        )

    assert skipped is None
    async with registry_db.session() as session:
        row = (await session.execute(select(Overlay).where(Overlay.id == overlay_id))).scalar_one()
    assert row.status == "confirmed"
    assert row.confirmed_revision_id == rematerialized_id
    assert await _overlay_deprecated_events(integration_context, overlay_id) == []


async def test_deprecate_superseded_overlay_cas_rejects_unlinked_rematerialize(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#1078: a re-materialize committed as current but not yet linked blocks the demote.

    The overlay still links its archived revision, so the guard and every overlay-row
    CAS predicate pass. Between the guard and the CAS, a re-materialize's ingest makes a
    fresh overlay-origin revision the API's current one (its relink is a later,
    separate step). The CAS checks the API row's current revision, so the overlay stays
    CONFIRMED and no event is emitted; the later relink then points a CONFIRMED overlay
    at its served revision.
    """
    handler = ImportHandler(integration_context)
    async with registry_db.session() as session:
        api = Api(vendor="rm-vendor", name="rm-api", version="1.0.0")
        session.add(api)
        await session.flush()
        archived = ApiRevision(
            api_id=api.id, state="archived", origin="overlay", spec_digest="sha256:old"
        )
        rematerialized = ApiRevision(
            api_id=api.id, state="archived", origin="overlay", spec_digest="sha256:new"
        )
        session.add_all([archived, rematerialized])
        await session.flush()
        overlay = Overlay(
            api_id=api.id,
            document=_OVERLAY_DOC,
            status="confirmed",
            confirmed_revision_id=archived.id,
            created_by="usr_author",
        )
        session.add(overlay)
        await session.commit()
        overlay_id, api_id, rematerialized_id = overlay.id, api.id, rematerialized.id
        archived_id = archived.id

    guard = ImportHandler._supersede_skip_reason

    async def _guard_then_rematerialize(
        session: Any, overlay: Overlay | None, superseding_api_id: uuid.UUID | None
    ) -> Any:
        reason = await guard(session, overlay, superseding_api_id)
        await session.execute(
            update(ApiRevision)
            .where(ApiRevision.id == rematerialized_id)
            .values(state="imported")
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(Api)
            .where(Api.id == api_id)
            .values(current_revision_id=rematerialized_id)
            .execution_options(synchronize_session=False)
        )
        return reason

    with patch.object(
        ImportHandler, "_supersede_skip_reason", staticmethod(_guard_then_rematerialize)
    ):
        skipped = await handler._deprecate_superseded_overlay(
            "job-rm",
            overlay_id,
            superseding_api_id=api_id,
            actor_id="usr_operator",
            actor_type="user",
        )

    assert skipped is None
    async with registry_db.session() as session:
        row = (await session.execute(select(Overlay).where(Overlay.id == overlay_id))).scalar_one()
    assert row.status == "confirmed"
    assert row.confirmed_revision_id == archived_id
    assert await _overlay_deprecated_events(integration_context, overlay_id) == []


async def test_deprecate_superseded_overlay_is_idempotent_under_repeat(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#940: a second deprecate of the same target is a safe no-op (CAS on CONFIRMED).

    Models two supersede handlers racing the demote (or a retry): the first flips the
    overlay CONFIRMED → DEPRECATED; the second observes it is no longer CONFIRMED, logs
    ``overlay_supersede_not_deprecated``, and changes nothing (no double-emit, no error).
    """
    handler = ImportHandler(integration_context)
    await _import_base(
        handler, vendor="idem-vendor", name="idem-api", version="1.0.0", origin="catalog"
    )
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.vendor == "idem-vendor"))).scalar_one()
        overlay = Overlay(
            api_id=api.id, document=_OVERLAY_DOC, status="confirmed", created_by="usr_author"
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    # First demote (no superseding_revision → uses the enqueue id directly).
    await handler._deprecate_superseded_overlay(
        "job-1", overlay_id, actor_id="usr_operator", actor_type="user"
    )
    async with registry_db.session() as session:
        after_first = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert after_first.status == "deprecated"
        first_deprecated_at = after_first.deprecated_at

    # Second demote: CAS on CONFIRMED fails (already DEPRECATED) → no-op, no error.
    await handler._deprecate_superseded_overlay(
        "job-2", overlay_id, actor_id="usr_operator", actor_type="user"
    )
    async with registry_db.session() as session:
        after_second = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert after_second.status == "deprecated"
        # Unchanged — the second call did not re-demote/rewrite the timestamp.
        assert after_second.deprecated_at == first_deprecated_at


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
        db=None,
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
        db=None,
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
        job_id=str(uuid.uuid4()), db=None, payload=job_payload, created_by="usr_test"
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
        job_id=str(uuid.uuid4()), db=None, payload=job_payload, created_by="usr_test"
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
            db=None,
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


def _mock_spec_client(spec_text: str) -> AsyncMock:
    """An httpx.AsyncClient double serving ``spec_text`` for any GET."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = spec_text
    mock_response.content = spec_text.encode()
    mock_response.headers = {"content-length": str(len(spec_text.encode()))}
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


async def test_catalog_import_lands_clean_identity_from_sub_segment(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#1020 end-to-end: a ``domain/sub`` catalog entry lands the clean identity.

    Threads the *real* production seam — ``CatalogService._to_import_source``
    builds the job source (sub-segment ``api_name`` + verbatim
    ``catalog_api_id``) and ``ImportHandler`` ingests it — and asserts the
    registry row is ``posthog-com/posthog-api``, not the vendor-doubled
    ``posthog-com/posthog-com-posthog-api`` the pre-fix full-id slug produced.
    """
    entry = CatalogEntryView(
        api_id="posthog.com/posthog-api",
        vendor="posthog.com",
        path=None,
        spec_url="https://catalog.example.com/posthog/openapi.json",
        github_url=None,
        registered=False,
    )
    source = CatalogService(integration_context)._to_import_source(
        entry, Identity(sub="usr_test", email="t@test.com", permissions=["org:admin"])
    )

    with patch(
        "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
        return_value=_mock_spec_client(MINIMAL_OPENAPI),
    ):
        result = await ImportHandler(integration_context).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={"sources": [source]},
            created_by="usr_test",
        )

    [revision] = result.body["revisions"]
    assert revision["api"]["vendor"] == "posthog-com"
    assert revision["api"]["name"] == "posthog-api"

    async with registry_db.session() as session:
        rows = (await session.execute(select(Api))).scalars().all()
        assert len(rows) == 1
        assert (rows[0].vendor, rows[0].name) == ("posthog-com", "posthog-api")
        assert rows[0].catalog_api_id == "posthog.com/posthog-api"


async def test_catalog_identity_conflict_surfaces_as_readable_job_failure(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """The #1020 collision guard reaches the operator through the job error.

    Two catalog ids collapsing to the same registry identity must fail the
    second import job with the guard's readable conflict message, leaving no
    new revision behind and the stored provenance untouched.
    """
    async with registry_db.session() as session:
        await ApiRepository.upsert(
            session,
            vendor="posthog-com",
            name="posthog-api",
            version="1.0.0",
            created_by="usr_test",
            catalog_api_id="posthog.com/posthog-api",
        )
        await session.commit()

    with (
        patch(
            "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
            return_value=_mock_spec_client(MINIMAL_OPENAPI),
        ),
        pytest.raises(IngestJobError, match="catalog identity conflict"),
    ):
        await ImportHandler(integration_context).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={
                "sources": [
                    {
                        "type": "url",
                        "url": "https://catalog.example.com/other/openapi.json",
                        "origin": "catalog",
                        # The manifest's extract_vendor reduces both hosts to the
                        # eTLD+1, so this entry carries the same vendor as the
                        # seeded one — same identity, different catalog id.
                        "vendor": "posthog.com",
                        "api_name": "posthog-api",
                        "catalog_api_id": "app.posthog.com/posthog-api",
                    }
                ]
            },
            created_by="usr_test",
        )

    async with registry_db.session() as session:
        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert revisions == []
        api = (await session.execute(select(Api))).scalar_one()
        assert api.catalog_api_id == "posthog.com/posthog-api"


async def test_catalog_reimport_keeps_pre_existing_doubled_identity(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A re-import of an API first imported under the pre-#1020 doubled name
    lands on that existing row instead of forking a clean-named sibling.

    Forking would strand every credential, binding, and permission rule keyed
    on the doubled identity on a row the catalog no longer updates. Re-slugging
    existing rows is the coordinated #1079 migration's job, not re-import's.
    """
    spec_url = "https://catalog.example.com/posthog/openapi.json"
    # Pre-fix import: the full catalog id seeded api_name, slugifying to the
    # vendor-doubled `posthog-com-posthog-api`.
    with patch(
        "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
        return_value=_mock_spec_client(MINIMAL_OPENAPI),
    ):
        await ImportHandler(integration_context).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={
                "sources": [
                    {
                        "type": "url",
                        "url": spec_url,
                        "origin": "catalog",
                        "vendor": "posthog.com",
                        "api_name": "posthog.com/posthog-api",
                        "catalog_api_id": "posthog.com/posthog-api",
                    }
                ]
            },
            created_by="usr_test",
        )

    entry = CatalogEntryView(
        api_id="posthog.com/posthog-api",
        vendor="posthog.com",
        path=None,
        spec_url=spec_url,
        github_url=None,
        registered=True,
    )
    svc = CatalogService(integration_context)
    registered = await svc._registered_identity(entry)
    assert registered == ("posthog-com", "posthog-com-posthog-api")
    source = svc._to_import_source(
        entry,
        Identity(sub="usr_test", email="t@test.com", permissions=["org:admin"]),
        vendor=registered[0],
        name=registered[1],
    )
    assert source["api_name"] == "posthog-com-posthog-api"

    # The upstream changed, so the re-import produces a genuinely new revision.
    updated_spec = json.loads(MINIMAL_OPENAPI)
    updated_spec["paths"]["/items"]["get"]["summary"] = "List all items"
    with patch(
        "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
        return_value=_mock_spec_client(json.dumps(updated_spec)),
    ):
        await ImportHandler(integration_context).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={"sources": [source]},
            created_by="usr_test",
        )

    async with registry_db.session() as session:
        rows = (await session.execute(select(Api))).scalars().all()
        assert [(r.vendor, r.name) for r in rows] == [("posthog-com", "posthog-com-posthog-api")]
        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(revisions) == 2
        assert {r.api_id for r in revisions} == {rows[0].id}


async def test_catalog_reimport_after_spec_url_move_keeps_identity_by_catalog_id(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """A doubled-name API whose manifest ``spec_url`` has since moved still
    re-imports onto its existing row.

    The moved URL makes the entry read as unregistered (``registered`` is
    spec_url-keyed), so a spec_url-only lookup would fall back to the clean
    sub-segment name and fork a second row carrying the same ``catalog_api_id``
    — stranding the credentials and rules keyed on the old one. The stored
    catalog id is the lookup that survives the move.
    """
    with patch(
        "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
        return_value=_mock_spec_client(MINIMAL_OPENAPI),
    ):
        await ImportHandler(integration_context).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={
                "sources": [
                    {
                        "type": "url",
                        "url": "https://catalog.example.com/posthog/old/openapi.json",
                        "origin": "catalog",
                        "vendor": "posthog.com",
                        "api_name": "posthog.com/posthog-api",
                        "catalog_api_id": "posthog.com/posthog-api",
                    }
                ]
            },
            created_by="usr_test",
        )

    entry = CatalogEntryView(
        api_id="posthog.com/posthog-api",
        vendor="posthog.com",
        path=None,
        spec_url="https://catalog.example.com/posthog/new/openapi.json",
        github_url=None,
        registered=False,
    )
    svc = CatalogService(integration_context)
    source = await svc._import_source_for(
        entry, Identity(sub="usr_test", email="t@test.com", permissions=["org:admin"])
    )
    assert (source["vendor"], source["api_name"]) == ("posthog-com", "posthog-com-posthog-api")

    updated_spec = json.loads(MINIMAL_OPENAPI)
    updated_spec["paths"]["/items"]["get"]["summary"] = "List all items"
    with patch(
        "jentic_one.registry.ingest.fetch.httpx.AsyncClient",
        return_value=_mock_spec_client(json.dumps(updated_spec)),
    ):
        await ImportHandler(integration_context).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={"sources": [source]},
            created_by="usr_test",
        )

    async with registry_db.session() as session:
        rows = (await session.execute(select(Api))).scalars().all()
        assert [(r.vendor, r.name) for r in rows] == [("posthog-com", "posthog-com-posthog-api")]


async def test_upsert_keeps_current_revision_loaded_in_the_session(
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """The locking read in ``ApiRepository.upsert`` must not cache a placeholder
    ``current_revision=None`` in the identity map: a later read in the same
    session has to see the served revision (and the lock must be legal on
    Postgres alongside the joined-eager outer join)."""
    async with registry_db.session() as session:
        api = await ApiRepository.upsert(
            session, vendor="acme-com", name="widgets", version="1.0.0", created_by="usr_test"
        )
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
        api.current_revision_id = revision.id
        await session.commit()

    async with registry_db.session() as session:
        locked = await ApiRepository.upsert(
            session, vendor="acme-com", name="widgets", version="1.0.0", created_by="usr_test"
        )
        assert locked.current_revision is not None
        again = await ApiRepository.get_by_identifier(session, "acme-com", "widgets", "1.0.0")
        assert again is not None
        assert again.current_revision is not None
        assert again.current_revision.id == locked.current_revision_id


async def test_supersede_gate_scopes_to_the_identity_the_reimport_lands_on(
    integration_context: Context,
    registry_db: DatabaseSession,
    _clean_registry: None,
) -> None:
    """#1078: the A4b gate and identity resolution pick the same row.

    The overlay lives on the doubled row, which carries the stored ``catalog_api_id``, so
    the re-import lands there. An older clean sibling serves a catalog revision from the
    same ``spec_url``, so an unscoped oldest-first lookup would resolve the sibling, find
    no overlay and skip the ``supersede_active`` stamp; the ingest on the doubled row
    would then fail on ``ix_api_revisions_one_active``.
    """
    handler = ImportHandler(integration_context)
    doubled_api_id, clean_api_id, overlay_id, _ = await _doubled_and_clean_rows(
        handler, registry_db
    )
    async with registry_db.session() as session:
        await session.execute(
            update(Api)
            .where(Api.id == doubled_api_id)
            .values(catalog_api_id="posthog.com/posthog-api")
        )
        await session.commit()
    async with registry_db.session() as session:
        unscoped = await ApiRevisionRepository.current_revision_for_source_url(
            session, _CATALOG_URL
        )
    assert unscoped is not None and unscoped[0] == clean_api_id

    entry = CatalogEntryView(
        api_id="posthog.com/posthog-api",
        vendor="posthog.com",
        path=None,
        spec_url=_CATALOG_URL,
        github_url=None,
        registered=True,
    )
    svc = CatalogService(integration_context)
    operator = Identity(sub="usr_operator", email="o@test.com", permissions=["overlays:confirm"])
    source = await svc._import_source_for(entry, operator)
    assert (source["vendor"], source["api_name"]) == ("posthog-com", "posthog-com-posthog-api")
    assert await svc._authorize_overlay_supersede(entry, operator) == overlay_id


async def test_import_job_result_reports_server_url_changes(
    integration_context: Context,
    _clean_registry: None,
) -> None:
    """The importer sees which declared servers ingest rewrote or dropped (#1241),
    so a server the stored spec no longer carries is never a silent change."""
    content = json.loads(MINIMAL_OPENAPI)
    content["servers"] = [{"url": "https://items.example.com/v1"}, {"url": "/v1"}]
    payload = {
        "sources": [
            {
                "type": "inline",
                "content": json.dumps(content),
                "filename": "openapi.json",
                "vendor": "servers-vendor",
                "api_name": "servers-api",
                "version": "1.0.0",
            }
        ]
    }

    # The DB handle is passed positionally so the call does not depend on its
    # parameter name.
    result = await ImportHandler(integration_context).execute(
        str(uuid.uuid4()), None, payload=payload, created_by="usr_test"
    )

    assert result.body["revisions"][0]["server_url_changes"] == {
        "resolved": [],
        "resolved_count": 0,
        "dropped": ["/v1"],
        "dropped_count": 1,
        "unresolved": [],
        "unresolved_count": 0,
    }
