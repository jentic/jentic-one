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
from jentic_one.registry.services.errors import OverlayStateConflictError
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.overlay_service import OverlayService
from jentic_one.shared.auth.identity import Identity
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
        session=None,
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
        session=None,
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
        session=None,
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
        session=None,
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
            session=None,
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
        session=None,
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
        session=None,
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
            session=None,
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
        session=None,
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
        session=None,
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
        session=None,
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
        session=None,
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
        session=None,
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
        session=None,
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
        session=None,
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
