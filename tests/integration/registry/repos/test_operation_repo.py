"""Integration tests for OperationRepository."""

from __future__ import annotations

import pytest
import structlog.testing
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.operation_repo import (
    OperationInput,
    OperationRepository,
    _generate_operation_id,
)
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


async def test_bulk_create_returns_deterministic_ids(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """bulk_create returns ids that are deterministic based on revision+path+method."""
    _, rev = sample_revision
    ops = [
        OperationInput(path="/users", method="GET"),
        OperationInput(path="/users", method="POST", summary="Create user"),
    ]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    assert len(ids) == 2
    assert ids[0] == _generate_operation_id(rev.id, "/users", "GET")
    assert ids[1] == _generate_operation_id(rev.id, "/users", "POST")


async def test_bulk_create_truncates_summary(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """bulk_create truncates summary to 500 chars."""
    _, rev = sample_revision
    long_summary = "x" * 600
    ops = [OperationInput(path="/long", method="GET", summary=long_summary)]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        results = await OperationRepository.get_by_ids(session, set(ids))
        assert len(results) == 1
        assert results[0].summary is not None
        assert len(results[0].summary) == 500


async def test_delete_for_revision_is_idempotent(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """delete_for_revision can be called even when no operations exist."""
    _, rev = sample_revision
    async with registry_db.session() as session:
        await OperationRepository.delete_for_revision(session, rev.id)
        await session.commit()


async def test_delete_for_revision_removes_operations(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """delete_for_revision removes all operations for the given revision."""
    _, rev = sample_revision
    ops = [OperationInput(path="/a", method="GET"), OperationInput(path="/b", method="POST")]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        await OperationRepository.delete_for_revision(session, rev.id)
        await session.commit()

    async with registry_db.session() as session:
        results = await OperationRepository.get_by_ids(session, set(ids))
        assert results == []


async def test_get_by_ids_eager_loads_servers(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """get_by_ids returns operations with servers eagerly loaded."""
    _, rev = sample_revision
    ops = [OperationInput(path="/srv", method="GET")]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        results = await OperationRepository.get_by_ids(session, set(ids))
        assert len(results) == 1
        assert results[0].servers is not None
        assert results[0].version_servers is not None


async def test_get_by_id_for_inspect_resolves_pk(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """The primary path resolves an operation by its registry PK."""
    _, rev = sample_revision
    ops = [OperationInput(path="/pk", method="GET", operation_id="spec.pk.get")]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        op = await OperationRepository.get_by_id_for_inspect(session, ids[0])
        assert op is not None
        assert op.id == ids[0]


async def test_get_by_id_for_inspect_falls_back_to_spec_operation_id(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """A spec operationId resolves via the fallback when it isn't a registry PK (#670)."""
    _, rev = sample_revision
    ops = [
        OperationInput(
            path="/v4/spreadsheets/{id}/values/{range}",
            method="GET",
            operation_id="sheets.spreadsheets.values.get",
        )
    ]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        # The fallback only considers live revisions, so promote the revision.
        await session.execute(
            update(ApiRevision).where(ApiRevision.id == rev.id).values(state="imported")
        )
        await session.commit()

    async with registry_db.session() as session:
        op = await OperationRepository.get_by_id_for_inspect(
            session, "sheets.spreadsheets.values.get"
        )
        assert op is not None
        assert op.id == ids[0]


async def test_get_by_id_for_inspect_spec_fallback_ignores_non_live_revision(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """A spec operationId on a draft/archived revision is NOT enumerable via inspect (#670)."""
    _, rev = sample_revision  # fixture state is "draft"
    spec_id = "sheets.spreadsheets.values.get"
    ops = [OperationInput(path="/v4/values/{range}", method="GET", operation_id=spec_id)]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        # PK lookup still works (explicit, authorized), spec-id fallback does not.
        assert await OperationRepository.get_by_id_for_inspect(session, ids[0]) is not None
        assert await OperationRepository.get_by_id_for_inspect(session, spec_id) is None


async def test_get_by_id_for_inspect_spec_collision_resolves_live_revision(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """A spec operationId on an archived AND the live revision resolves the live one (#670).

    A partial unique index allows only one live (published/imported) revision per
    API, so the realistic collision is an archived prior revision plus the live
    current one — the fallback must return the live operation, not the archived.
    """
    api, old_rev = sample_revision
    spec_id = "sheets.spreadsheets.values.get"
    ops = [OperationInput(path="/v4/values/{range}", method="GET", operation_id=spec_id)]

    async with registry_db.session() as session:
        # The original revision becomes an archived prior version...
        old_ids = await OperationRepository.bulk_create(
            session, old_rev.id, ops, created_by="usr_test"
        )
        await session.execute(
            update(ApiRevision).where(ApiRevision.id == old_rev.id).values(state="archived")
        )
        # ...superseded by a live (published) current revision.
        current_rev = ApiRevision(
            api_id=api.id,
            state="published",
            spec_digest="sha256:def456",
            source_type="url",
        )
        session.add(current_rev)
        await session.flush()
        current_ids = await OperationRepository.bulk_create(
            session, current_rev.id, ops, created_by="usr_test"
        )
        await session.execute(
            update(Api).where(Api.id == api.id).values(current_revision_id=current_rev.id)
        )
        await session.commit()

    assert old_ids[0] != current_ids[0]

    async with registry_db.session() as session:
        op = await OperationRepository.get_by_id_for_inspect(session, spec_id)
        assert op is not None
        assert op.id == current_ids[0], "should resolve the operation on the live revision"


async def test_get_by_id_for_inspect_spec_collision_across_apis_is_ambiguous(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """A spec operationId that collides across *different* APIs resolves to nothing (#670).

    Spec operationIds are unique only within an API, so a cross-API collision is
    ambiguous and must not silently return an arbitrary vendor's operation —
    inspect should 404 and steer the agent to the registry ``operation_id``.
    """
    api_a, rev_a = sample_revision
    spec_id = "getStatus"
    ops = [OperationInput(path="/status", method="GET", operation_id=spec_id)]

    async with registry_db.session() as session:
        await OperationRepository.bulk_create(session, rev_a.id, ops, created_by="usr_test")
        await session.execute(
            update(ApiRevision).where(ApiRevision.id == rev_a.id).values(state="published")
        )
        await session.execute(
            update(Api).where(Api.id == api_a.id).values(current_revision_id=rev_a.id)
        )

        # A second, distinct API exposing the same spec operationId on its
        # current/live revision.
        api_b = Api(vendor="other.com", name="other-api", version="v1")
        session.add(api_b)
        await session.flush()
        rev_b = ApiRevision(
            api_id=api_b.id,
            state="published",
            spec_digest="sha256:bbb222",
            source_type="url",
        )
        session.add(rev_b)
        await session.flush()
        await OperationRepository.bulk_create(session, rev_b.id, ops, created_by="usr_test")
        await session.execute(
            update(Api).where(Api.id == api_b.id).values(current_revision_id=rev_b.id)
        )
        await session.commit()

    async with registry_db.session() as session:
        assert await OperationRepository.get_by_id_for_inspect(session, spec_id) is None


async def test_get_by_id_for_inspect_unknown_returns_none(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """An id that is neither a PK nor a known spec operationId resolves to None."""
    _, rev = sample_revision
    ops = [OperationInput(path="/x", method="GET", operation_id="real.op")]

    async with registry_db.session() as session:
        await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        assert await OperationRepository.get_by_id_for_inspect(session, "does.not.exist") is None
        # A non-existent PK form short-circuits without a fallback lookup.
        assert await OperationRepository.get_by_id_for_inspect(session, "op_missing") is None


async def test_revision_path_method_uniqueness(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """(revision_id, path, method) uniqueness is enforced."""
    _, rev = sample_revision
    ops = [OperationInput(path="/dup", method="GET")]

    async with registry_db.session() as session:
        await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    with pytest.raises(IntegrityError):
        async with registry_db.session() as session:
            await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
            await session.commit()


async def test_bulk_create_coerces_duplicate_and_over_long_operation_ids(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """Pins #1437: duplicate and over-long spec operationIds are coerced to
    unique, column-sized values rather than raising IntegrityError/DataError.

    The first holder keeps the id, later duplicates get ``_<n>`` suffixes that
    skip ids the spec declares itself (``dup_2``), and over-long ids are
    truncated to the column length — including when the suffix must fit."""
    _, rev = sample_revision
    long_id = "L" * 400
    ops = [
        OperationInput(path="/a", method="GET", operation_id="dup"),
        OperationInput(path="/b", method="GET", operation_id="dup"),
        OperationInput(path="/c", method="GET", operation_id="dup_2"),
        OperationInput(path="/d", method="GET", operation_id="dup"),
        OperationInput(path="/e", method="GET", operation_id=long_id),
        OperationInput(path="/f", method="GET", operation_id=long_id),
        OperationInput(path="/g", method="GET"),
        OperationInput(path="/h", method="GET"),
    ]

    async with registry_db.session() as session:
        ids = await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
        await session.commit()

    async with registry_db.session() as session:
        stored = await OperationRepository.list_for_revision(session, revision_id=rev.id)
    assert len(ids) == len(ops)
    assert {op.path: op.operation_id for op in stored} == {
        "/a": "dup",
        "/b": "dup_3",
        "/c": "dup_2",
        "/d": "dup_4",
        "/e": "L" * 255,
        "/f": "L" * 253 + "_2",
        "/g": None,
        "/h": None,
    }


async def test_bulk_create_stores_blank_and_non_string_operation_ids_as_null(
    registry_db: DatabaseSession, sample_revision: tuple[Api, ApiRevision]
) -> None:
    """Pins #1437: repeated empty/whitespace-only or non-string operationIds
    are stored as NULL rather than colliding on the unique constraint (or
    raising TypeError on a malformed spec's integer id)."""
    _, rev = sample_revision
    ops = [
        OperationInput(path="/e1", method="GET", operation_id=""),
        OperationInput(path="/e2", method="GET", operation_id=""),
        OperationInput(path="/w1", method="GET", operation_id="   "),
        OperationInput(path="/w2", method="GET", operation_id="   "),
        OperationInput(path="/n1", method="GET", operation_id=42),  # type: ignore[arg-type]
        OperationInput(path="/n2", method="GET", operation_id=42),  # type: ignore[arg-type]
    ]

    with structlog.testing.capture_logs() as logs:
        async with registry_db.session() as session:
            await OperationRepository.bulk_create(session, rev.id, ops, created_by="usr_test")
            await session.commit()

    async with registry_db.session() as session:
        stored = await OperationRepository.list_for_revision(session, revision_id=rev.id)
    assert {op.path: op.operation_id for op in stored} == {op.path: None for op in ops}
    reasons = sorted(e["reasons"][0] for e in logs if e["event"] == "spec_operation_id_coerced")
    assert reasons == ["blank"] * 4 + ["not_string"] * 2
