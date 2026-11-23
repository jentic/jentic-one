"""Legacy free-host URL-index rows cannot route credential-bearing calls."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_VENDOR = "url-lookup-host-binding.example"
_NAME = "widgets"
_VERSION = "1.0.0"
_OPERATION_ID = "op_url_lookup_host_binding"


@pytest.fixture()
async def clean_host_lookup_registry(
    registry_db: DatabaseSession,
) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with registry_db.session() as session:
            revision_ids = (
                select(ApiRevision.id)
                .join(Api, Api.id == ApiRevision.api_id)
                .where(Api.vendor == _VENDOR)
            )
            await session.execute(
                delete(OperationURLIndex).where(OperationURLIndex.revision_id.in_(revision_ids))
            )
            await session.execute(delete(Operation).where(Operation.revision_id.in_(revision_ids)))
            await session.execute(
                update(Api).where(Api.vendor == _VENDOR).values(current_revision_id=None)
            )
            await session.execute(
                delete(ApiRevision).where(
                    ApiRevision.api_id.in_(select(Api.id).where(Api.vendor == _VENDOR))
                )
            )
            await session.execute(delete(Api).where(Api.vendor == _VENDOR))
            await session.commit()

    await _clean()
    yield
    await _clean()


async def _seed_legacy_row(
    registry_db: DatabaseSession, *, method: str = "GET", host_regex: str = "^[^:/]+$"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a live operation and a regex-only row in the legacy free-host format."""
    api = Api(vendor=_VENDOR, name=_NAME, version=_VERSION)
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="inline")
        session.add(revision)
        await session.flush()
        api.current_revision_id = revision.id
        revision_id = revision.id
        await session.commit()

    row_id = uuid.uuid4()
    async with registry_db.session() as session:
        session.add(
            Operation(
                id=_OPERATION_ID,
                revision_id=revision_id,
                operation_id="listUsers",
                path="/api/users",
                method=method,
            )
        )
        session.add(
            OperationURLIndex(
                id=row_id,
                operation_id=_OPERATION_ID,
                revision_id=revision_id,
                method=method,
                host=None,
                host_regex=host_regex,
                path_template="/api/users",
                path_regex="^/api/users$",
                param_names=[],
                segment_count=2,
            )
        )
        await session.commit()
    return revision_id, row_id


@pytest.mark.parametrize("host_regex", ["^[^:/]+$", "^[^/]+$"])
@pytest.mark.asyncio
async def test_legacy_free_host_row_is_ignored_but_literal_host_still_resolves(
    registry_db: DatabaseSession, clean_host_lookup_registry: None, host_regex: str
) -> None:
    _revision_id, row_id = await _seed_legacy_row(registry_db, host_regex=host_regex)

    async with registry_db.session() as session:
        unresolved = await URLLookupService(session).resolve(
            method="GET", url="https://attacker.example/api/users"
        )
    assert unresolved is None

    async with registry_db.session() as session:
        row = await session.get(OperationURLIndex, row_id)
        assert row is not None
        row.host = "{tenant}"
        await session.commit()

    async with registry_db.session() as session:
        literal = await URLLookupService(session).resolve(
            method="GET", url="https://{tenant}/api/users"
        )
    assert literal is not None
    assert literal.operation_id == _OPERATION_ID


@pytest.mark.parametrize("host_regex", ["^[^:/]+$", "^[^/]+$"])
@pytest.mark.asyncio
async def test_legacy_free_host_row_does_not_contribute_allowed_methods(
    registry_db: DatabaseSession, clean_host_lookup_registry: None, host_regex: str
) -> None:
    revision_id, _row_id = await _seed_legacy_row(registry_db, method="POST", host_regex=host_regex)

    async with registry_db.session() as session:
        result = await URLLookupService(session).resolve(
            method="GET",
            url="https://attacker.example/api/users",
            revision_id=revision_id,
        )
    assert result is None
