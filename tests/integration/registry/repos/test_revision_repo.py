"""Integration tests for ApiRevisionRepository."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState

pytestmark = pytest.mark.integration


async def test_create_draft(registry_db: DatabaseSession, sample_api: Api) -> None:
    """create_draft creates a revision with state='draft'."""
    async with registry_db.session() as session:
        rev = await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest="sha256:deadbeef",
            source_type=ApiRevisionSourceType.URL,
            source_url="https://example.com/spec.yaml",
            submitted_by="test-user",
            created_by="usr_test",
        )
        await session.commit()

    assert rev.id is not None
    assert rev.state == "draft"
    assert rev.api_id == sample_api.id
    assert rev.spec_digest == "sha256:deadbeef"
    assert rev.source_type == ApiRevisionSourceType.URL
    assert rev.source_url == "https://example.com/spec.yaml"
    assert rev.submitted_by == "test-user"
    assert rev.operation_count == 0


async def test_get_by_digest_found(registry_db: DatabaseSession, sample_api: Api) -> None:
    """get_by_digest returns a matching revision."""
    async with registry_db.session() as session:
        await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest="sha256:findme",
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        await session.commit()

    async with registry_db.session() as session:
        found = await ApiRevisionRepository.get_by_digest(session, sample_api.id, "sha256:findme")
        assert found is not None
        assert found.spec_digest == "sha256:findme"


async def test_get_by_digest_not_found(registry_db: DatabaseSession, sample_api: Api) -> None:
    """get_by_digest returns None when no match exists."""
    async with registry_db.session() as session:
        found = await ApiRevisionRepository.get_by_digest(
            session, sample_api.id, "sha256:nonexistent"
        )
        assert found is None


async def test_digest_uniqueness_constraint(registry_db: DatabaseSession, sample_api: Api) -> None:
    """Duplicate (api_id, spec_digest) raises IntegrityError."""
    async with registry_db.session() as session:
        await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest="sha256:dupe",
            source_type=ApiRevisionSourceType.URL,
            created_by="usr_test",
        )
        await session.commit()

    with pytest.raises(IntegrityError):
        async with registry_db.session() as session:
            await ApiRevisionRepository.create_draft(
                session,
                api_id=sample_api.id,
                spec_digest="sha256:dupe",
                source_type=ApiRevisionSourceType.INLINE,
                created_by="usr_test",
            )
            await session.commit()


async def test_null_digests_do_not_collide(registry_db: DatabaseSession, sample_api: Api) -> None:
    """Two sha-less revisions (spec_digest=NULL) coexist under the unique constraint (#780).

    NULLs are distinct under uq_api_revisions_api_id_spec_digest on both Postgres
    and SQLite — unlike '' (a value), which would collapse distinct sha-less specs
    into one revision.
    """
    async with registry_db.session() as session:
        first = await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        second = await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        await session.commit()

    assert first.id != second.id
    async with registry_db.session() as session:
        first_loaded = await session.get(ApiRevision, first.id)
        second_loaded = await session.get(ApiRevision, second.id)
        assert first_loaded is not None and first_loaded.spec_digest is None
        assert second_loaded is not None and second_loaded.spec_digest is None


async def _archived_revision(
    session: AsyncSession, api_id: uuid.UUID, *, origin: str | None
) -> ApiRevision:
    """Create a revision, drive it active then ARCHIVED, mirroring a superseded base.

    ``origin=None`` stands in for a manually-promoted PUBLISHED base (``create_draft``
    never sets origin); a non-None origin stands in for an IMPORTED/catalog base.
    """
    if origin is None:
        rev = await ApiRevisionRepository.create_draft(
            session,
            api_id=api_id,
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
    else:
        rev = await ApiRevisionRepository.create_imported(
            session,
            api_id=api_id,
            origin=origin,
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
    await ApiRevisionRepository.set_state(session, rev.id, ApiRevisionState.ARCHIVED)
    return rev


async def test_restore_archived_published_base_stays_published(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A PUBLISHED (origin-less) base restores to PUBLISHED, not IMPORTED (#939)."""
    async with registry_db.session() as session:
        rev = await _archived_revision(session, sample_api.id, origin=None)
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        rowcount = await ApiRevisionRepository.restore_archived(session, rev_id)
        await session.commit()

    assert rowcount == 1
    async with registry_db.session() as session:
        loaded = await session.get(ApiRevision, rev_id)
        assert loaded is not None
        assert loaded.state == ApiRevisionState.PUBLISHED
        assert loaded.archived_at is None


async def test_restore_archived_imported_base_stays_imported(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """An IMPORTED (origin-bearing) base restores to IMPORTED (#939)."""
    async with registry_db.session() as session:
        rev = await _archived_revision(session, sample_api.id, origin="catalog")
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        rowcount = await ApiRevisionRepository.restore_archived(session, rev_id)
        await session.commit()

    assert rowcount == 1
    async with registry_db.session() as session:
        loaded = await session.get(ApiRevision, rev_id)
        assert loaded is not None
        assert loaded.state == ApiRevisionState.IMPORTED
        assert loaded.archived_at is None


async def test_restore_archived_non_archived_is_noop(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """CAS on state==ARCHIVED: a non-archived revision is left untouched (rowcount 0)."""
    async with registry_db.session() as session:
        rev = await ApiRevisionRepository.create_imported(
            session,
            api_id=sample_api.id,
            origin="catalog",
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        rowcount = await ApiRevisionRepository.restore_archived(session, rev_id)
        await session.commit()

    assert rowcount == 0
    async with registry_db.session() as session:
        loaded = await session.get(ApiRevision, rev_id)
        assert loaded is not None
        assert loaded.state == ApiRevisionState.IMPORTED


async def test_restore_archived_draft_is_noop(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A DRAFT (origin-less, non-archived) row is left untouched — not flipped to PUBLISHED.

    The dangerous branch for the state CASE: a DRAFT is ``origin IS NULL``, so if the CAS
    on ``state == ARCHIVED`` were ever weakened, restore would wrongly promote it to
    PUBLISHED. Pin the CAS specifically against the origin-less branch (#939).
    """
    async with registry_db.session() as session:
        rev = await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        rowcount = await ApiRevisionRepository.restore_archived(session, rev_id)
        await session.commit()

    assert rowcount == 0
    async with registry_db.session() as session:
        loaded = await session.get(ApiRevision, rev_id)
        assert loaded is not None
        assert loaded.state == ApiRevisionState.DRAFT


async def test_restore_archived_missing_revision_returns_zero(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A missing revision id yields rowcount 0 — the contract rollback maps to
    OverlayRollbackTargetMissingError (#939)."""
    async with registry_db.session() as session:
        rowcount = await ApiRevisionRepository.restore_archived(session, uuid.uuid4())
        await session.commit()
    assert rowcount == 0


async def test_state_origin_invariant_holds_for_producers(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """Guard the load-bearing invariant `restore_archived` relies on (#939).

    The state CASE reconstructs prior state from `origin` (NULL ⇒ PUBLISHED, else
    IMPORTED). That is only valid because the two revision producers correlate state and
    origin: create_draft (the sole origin-less creator; DRAFT→PUBLISHED via promote) never
    sets origin, and create_imported (the sole IMPORTED producer) always sets one. If a
    future producer breaks this (e.g. an origin-less IMPORTED, or an origin-bearing row
    that later becomes PUBLISHED), rollback would silently mis-restore. Fail loudly here.
    """
    async with registry_db.session() as session:
        draft = await ApiRevisionRepository.create_draft(
            session,
            api_id=sample_api.id,
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        # A DRAFT promoted to PUBLISHED keeps origin NULL (promote never sets origin).
        await ApiRevisionRepository.set_state(session, draft.id, ApiRevisionState.PUBLISHED)
        await session.commit()
        published_id = draft.id

    async with registry_db.session() as session:
        published = await session.get(ApiRevision, published_id)
        assert published is not None and published.state == ApiRevisionState.PUBLISHED
        assert published.origin is None, "PUBLISHED revision must have origin IS NULL (#939)"
        # Archive it before adding an IMPORTED row: the one-active partial unique index
        # forbids two active (published/imported) revisions for the same api_id.
        await ApiRevisionRepository.set_state(session, published_id, ApiRevisionState.ARCHIVED)
        imported = await ApiRevisionRepository.create_imported(
            session,
            api_id=sample_api.id,
            origin="catalog",
            spec_digest=None,
            source_type=ApiRevisionSourceType.INLINE,
            created_by="usr_test",
        )
        await session.commit()
        imported_id = imported.id

    async with registry_db.session() as session:
        imported_row = await session.get(ApiRevision, imported_id)
        assert imported_row is not None and imported_row.state == ApiRevisionState.IMPORTED
        assert imported_row.origin is not None, (
            "IMPORTED revision must have a non-NULL origin (#939)"
        )


async def test_set_operation_count(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """set_operation_count updates the operation_count field."""
    _, rev = sample_revision
    async with registry_db.session() as session:
        await ApiRevisionRepository.set_operation_count(session, rev.id, 15)
        await session.commit()

    async with registry_db.session() as session:
        loaded = await session.get(ApiRevision, rev.id)
        assert loaded is not None
        assert loaded.operation_count == 15
