"""Integration tests for ``RevisionService.promote`` against a real registry DB.

Regression coverage for jentic/jentic-one#642: promoting a draft revision when
another revision is already live must return the refreshed API view (host +
security schemes of the newly-live revision) and must not raise
``MissingGreenlet`` from an async lazy load on a stale, bulk-updated instance.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.services.errors import RevisionStateConflictError
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionState

pytestmark = pytest.mark.integration

_IDENTITY = Identity(sub="usr_test", email="test@example.com")


@pytest.fixture()
async def clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Truncate the registry tables this module touches, before and after each test."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(Server))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _create_revision(
    session,
    *,
    api_id: uuid.UUID,
    state: str,
    spec_digest: str,
    host: str | None = None,
    scheme_type: str | None = None,
) -> uuid.UUID:
    """Create a revision (optionally with a server host and security scheme)."""
    revision = ApiRevision(
        api_id=api_id,
        state=state,
        spec_digest=spec_digest,
        source_type="url",
        source_url="https://example.com/spec.yaml",
        created_by="usr_test",
    )
    session.add(revision)
    await session.flush()
    if host is not None:
        session.add(Server(revision_id=revision.id, url=f"https://{host}", created_by="usr_test"))
    if scheme_type is not None:
        session.add(
            SecurityScheme(
                revision_id=revision.id,
                name="default",
                type=scheme_type,
                raw_scheme={"type": scheme_type},
                created_by="usr_test",
            )
        )
    await session.flush()
    return revision.id


@pytest.fixture()
async def api_with_live_and_draft(
    registry_db: DatabaseSession, clean_registry: None
) -> tuple[str, str, str, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed an API with a live (published) revision A and a draft revision B.

    Returns ``(vendor, name, version, api_id, live_id, draft_id)``.
    """
    api = Api(vendor="acme.com", name="widget", version="v1", created_by="usr_test")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        live_id = await _create_revision(
            session,
            api_id=api.id,
            state=ApiRevisionState.PUBLISHED,
            spec_digest="sha256:live",
            host="old.acme.com",
            scheme_type="apiKey",
        )
        draft_id = await _create_revision(
            session,
            api_id=api.id,
            state=ApiRevisionState.DRAFT,
            spec_digest="sha256:draft",
            host="new.acme.com",
            scheme_type="http",
        )
        api.current_revision_id = live_id
        await session.commit()
    return api.vendor, api.name, api.version, api.id, live_id, draft_id


async def test_promote_over_live_returns_refreshed_view(
    integration_context: Context,
    registry_db: DatabaseSession,
    api_with_live_and_draft: tuple[str, str, str, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Promoting a draft over a live revision succeeds and returns the new live view.

    Before the #642 fix this raised ``sqlalchemy.exc.MissingGreenlet`` because
    ``_fetch_api_view`` accessed relationships on a stale, bulk-updated ``Api``.
    """
    vendor, name, version, api_id, live_id, draft_id = api_with_live_and_draft

    svc = RevisionService(integration_context)
    view = await svc.promote(vendor, name, version, str(draft_id), identity=_IDENTITY)

    # The view reflects the newly-live draft, not the previously-live revision.
    assert view.current_revision_id == str(draft_id)
    assert view.host == "new.acme.com"
    assert view.security_schemes == ["http"]

    # Persisted state: draft is now published/live, old live is archived.
    async with registry_db.session() as session:
        api = await session.get(Api, api_id)
        assert api is not None
        assert api.current_revision_id == draft_id

        new_live = await session.get(ApiRevision, draft_id)
        old_live = await session.get(ApiRevision, live_id)
        assert new_live is not None
        assert old_live is not None
        assert new_live.state == ApiRevisionState.PUBLISHED
        assert new_live.promoted_at is not None
        assert old_live.state == ApiRevisionState.ARCHIVED
        assert old_live.archived_at is not None


async def test_promote_first_revision_no_live_still_works(
    integration_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Promoting the first-ever revision (no live yet) still works (no regression)."""
    api = Api(vendor="acme.com", name="gadget", version="v1", created_by="usr_test")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        draft_id = await _create_revision(
            session,
            api_id=api.id,
            state=ApiRevisionState.DRAFT,
            spec_digest="sha256:first",
            host="first.acme.com",
            scheme_type="apiKey",
        )
        await session.commit()

    svc = RevisionService(integration_context)
    view = await svc.promote(api.vendor, api.name, api.version, str(draft_id), identity=_IDENTITY)

    assert view.current_revision_id == str(draft_id)
    assert view.host == "first.acme.com"
    assert view.security_schemes == ["apiKey"]

    async with registry_db.session() as session:
        loaded = await session.get(ApiRevision, draft_id)
        assert loaded is not None
        assert loaded.state == ApiRevisionState.PUBLISHED


async def test_promote_already_published_raises_conflict(
    integration_context: Context,
    api_with_live_and_draft: tuple[str, str, str, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Promoting an already-published revision is a state conflict, not a 500."""
    vendor, name, version, _api_id, live_id, _draft_id = api_with_live_and_draft

    svc = RevisionService(integration_context)
    with pytest.raises(RevisionStateConflictError):
        await svc.promote(vendor, name, version, str(live_id), identity=_IDENTITY)


async def test_promote_updates_operation_count_and_archive_resets_it(
    integration_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Promoting drafts updates Api.operation_count; archiving current resets it to 0."""
    api = Api(
        vendor="acme.com",
        name="ops-counter",
        version="v1",
        operation_count=0,
        created_by="usr_test",
    )
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        draft_v1 = ApiRevision(
            api_id=api.id,
            state=ApiRevisionState.DRAFT,
            origin="catalog",
            spec_digest="sha256:ops-v1",
            source_type="url",
            source_url="https://example.com/v1.yaml",
            operation_count=5,
            created_by="usr_test",
        )
        draft_v2 = ApiRevision(
            api_id=api.id,
            state=ApiRevisionState.DRAFT,
            origin="catalog",
            spec_digest="sha256:ops-v2",
            source_type="url",
            source_url="https://example.com/v2.yaml",
            operation_count=9,
            created_by="usr_test",
        )
        session.add_all([draft_v1, draft_v2])
        await session.commit()
        api_id = api.id
        v1_id = draft_v1.id
        v2_id = draft_v2.id

    svc = RevisionService(integration_context)

    # First promotion: 0 -> 5
    view1 = await svc.promote(api.vendor, api.name, api.version, str(v1_id), identity=_IDENTITY)
    assert view1.operation_count == 5
    async with registry_db.session() as session:
        loaded = await session.get(Api, api_id)
        assert loaded is not None
        assert loaded.operation_count == 5

    # Second promotion across drafts: 5 -> 9
    view2 = await svc.promote(api.vendor, api.name, api.version, str(v2_id), identity=_IDENTITY)
    assert view2.operation_count == 9
    async with registry_db.session() as session:
        loaded = await session.get(Api, api_id)
        assert loaded is not None
        assert loaded.operation_count == 9

    # Archiving the current revision clears current_revision_id and resets operation_count to 0.
    await svc.archive(api.vendor, api.name, api.version, str(v2_id), identity=_IDENTITY)
    async with registry_db.session() as session:
        loaded = await session.get(Api, api_id)
        assert loaded is not None
        assert loaded.current_revision_id is None
        assert loaded.operation_count == 0


async def test_concurrent_promote_serializes_cleanly(
    integration_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Two concurrent promotes of the same draft yield one success and one 409 conflict."""
    api = Api(vendor="acme.com", name="race-promote", version="v1", created_by="usr_test")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        draft_id = await _create_revision(
            session,
            api_id=api.id,
            state=ApiRevisionState.DRAFT,
            spec_digest="sha256:race-draft",
            host="race.acme.com",
            scheme_type="http",
        )
        await session.commit()
        api_id = api.id

    svc = RevisionService(integration_context)
    outcomes = await asyncio.gather(
        svc.promote(api.vendor, api.name, api.version, str(draft_id), identity=_IDENTITY),
        svc.promote(api.vendor, api.name, api.version, str(draft_id), identity=_IDENTITY),
        return_exceptions=True,
    )
    conflicts = [o for o in outcomes if isinstance(o, RevisionStateConflictError)]
    successes = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with registry_db.session() as session:
        loaded = await session.get(Api, api_id)
        assert loaded is not None
        assert loaded.current_revision_id == draft_id
