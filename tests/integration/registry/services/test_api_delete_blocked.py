"""Integration tests: API delete vs. references from outside the OSS schema.

Deleting an API must succeed when an extension table hangs off a revision
(``ON DELETE CASCADE`` on ``revision_id``) but also carries a non-cascading FK
onto ``apis``. With a single ``DELETE FROM apis`` Postgres checks that NO
ACTION FK before the nested ``apis -> api_revisions -> table`` cascade has
removed the rows, so the delete was always refused — the API was undeletable,
surfacing only as an opaque 409 "conflicts with the current state". The
repository now deletes the revisions in their own statement first.

A reference that genuinely blocks the delete surfaces as
``ApiDeleteBlockedError`` (409 ``api_delete_blocked``), the API left intact.

Both scenarios need DDL for an ad-hoc referencing table and real FK
enforcement, so they run on Postgres only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, text

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.services.api_service import ApiService
from jentic_one.registry.services.errors import ApiDeleteBlockedError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_VENDOR = "apidelblocked.example"
_NAME = "derived-api"
_VERSION = "v1"
_EXT_TABLE = "zz_test_api_delete_ext"

_IDENTITY = Identity(sub="usr_test", actor_type=ActorType.USER, permissions=["org:admin"])


@pytest.fixture()
async def ext_table(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    if registry_db.backend.dialect_name == "sqlite":
        pytest.skip("ad-hoc FK DDL + cascade/NO ACTION interplay are a Postgres concern")

    async def _cleanup() -> None:
        async with registry_db.session() as session:
            await session.execute(text(f"DROP TABLE IF EXISTS {_EXT_TABLE}"))
            await session.execute(delete(Api).where(Api.vendor == _VENDOR))
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _create_ext_table(registry_db: DatabaseSession, *, revision_fk: bool) -> None:
    """A referencing table shaped like derived per-revision extension data.

    ``api_id`` is NO ACTION (no ``ON DELETE``); ``revision_id`` cascades when
    ``revision_fk`` is set, otherwise it is a plain column.
    """
    revision_col = (
        "revision_id uuid NOT NULL REFERENCES api_revisions (id) ON DELETE CASCADE"
        if revision_fk
        else "revision_id uuid NOT NULL"
    )
    async with registry_db.session() as session:
        await session.execute(
            text(
                f"CREATE TABLE {_EXT_TABLE} ("
                "id uuid PRIMARY KEY, "
                f"{revision_col}, "
                f"api_id uuid NOT NULL CONSTRAINT fk_{_EXT_TABLE}_api REFERENCES apis (id))"
            )
        )
        await session.commit()


async def _seed(registry_db: DatabaseSession) -> tuple[uuid.UUID, uuid.UUID]:
    async with registry_db.session() as session:
        api = Api(vendor=_VENDOR, name=_NAME, version=_VERSION)
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, created_by="usr_test")
        session.add(revision)
        await session.flush()
        api.current_revision_id = revision.id
        await session.execute(
            text(f"INSERT INTO {_EXT_TABLE} (id, revision_id, api_id) VALUES (:id, :rev, :api)"),
            {"id": uuid.uuid4(), "rev": revision.id, "api": api.id},
        )
        await session.commit()
        return api.id, revision.id


async def _count(registry_db: DatabaseSession, sql: str, **params: object) -> int:
    async with registry_db.session() as session:
        return int((await session.execute(text(sql), params)).scalar_one())


async def test_delete_succeeds_when_ext_rows_cascade_via_revision(
    integration_context: Context, registry_db: DatabaseSession, ext_table: None
) -> None:
    """NO ACTION onto ``apis`` + CASCADE onto the revision: the delete succeeds
    and the derived rows go with it (refused before the revisions-first fix)."""
    await _create_ext_table(registry_db, revision_fk=True)
    api_id, _revision_id = await _seed(registry_db)

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    async with registry_db.session() as session:
        assert (await session.execute(select(Api).where(Api.id == api_id))).first() is None
    assert await _count(registry_db, f"SELECT count(*) FROM {_EXT_TABLE}") == 0


async def test_genuinely_blocking_reference_raises_api_delete_blocked(
    integration_context: Context, registry_db: DatabaseSession, ext_table: None
) -> None:
    """A reference with no cascade path refuses the delete with a typed error
    naming the blocking constraint, and rolls the whole delete back."""
    await _create_ext_table(registry_db, revision_fk=False)
    api_id, revision_id = await _seed(registry_db)

    with pytest.raises(ApiDeleteBlockedError) as excinfo:
        await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    assert excinfo.value.constraint == f"fk_{_EXT_TABLE}_api"
    assert excinfo.value.table == _EXT_TABLE
    # Atomic: neither the API nor its revision was removed.
    async with registry_db.session() as session:
        api = (await session.execute(select(Api).where(Api.id == api_id))).scalar_one()
        assert api.current_revision_id == revision_id
        assert (
            await session.execute(select(ApiRevision).where(ApiRevision.id == revision_id))
        ).first() is not None
