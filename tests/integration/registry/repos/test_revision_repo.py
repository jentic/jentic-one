"""Integration tests for ApiRevisionRepository."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType

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
