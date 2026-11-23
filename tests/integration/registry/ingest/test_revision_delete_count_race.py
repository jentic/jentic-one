"""Integration test: deleting a revision an identical re-import just replaced.

Pins #1980: ``RevisionService.delete`` reads the archived revision, then
deletes it. A catalog re-import of identical content that commits in between
replaces that archived revision (and gives its count back). The delete must
then report the revision as gone and leave ``apis.revision_count`` equal to the
number of revision rows, rather than decrementing a second time.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.services.errors import RevisionNotFoundError
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite",
        reason="needs a second writer mid-transaction; SQLite serializes writers",
    ),
]

_IDENTIFIER = ApiIdentifier(vendor="delrace.example.com", name="api", version="1.0.0")
_IDENTITY = Identity(sub="usr_test", email="test@example.com")


def _spec(sha: str) -> IngestSpecification:
    content: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": f"Delete race {sha}", "version": "1.0.0"},
        "servers": [{"url": "https://api.delrace.example.com"}],
        "paths": {
            "/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}}
        },
    }
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=_IDENTIFIER,
        sha=sha,
        content=content,
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        origin="catalog",
    )


async def _count_and_rows(registry_db: DatabaseSession) -> tuple[int, int]:
    async with registry_db.session() as session:
        api = (
            await session.execute(
                select(Api).where(Api.vendor == _IDENTIFIER.vendor, Api.name == _IDENTIFIER.name)
            )
        ).scalar_one()
        rows = (
            await session.execute(
                select(func.count()).select_from(ApiRevision).where(ApiRevision.api_id == api.id)
            )
        ).scalar_one()
        return api.revision_count, rows


async def test_delete_of_revision_replaced_mid_flight_keeps_count_equal_to_rows(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = Ingestor(ingest_context)
    first = await ingestor.ingest(_spec("sha-a"), created_by="usr")
    await ingestor.ingest(_spec("sha-b"), created_by="usr")
    async with registry_db.session() as session:
        archived = await session.get(ApiRevision, first.revision_id)
        assert archived is not None
        assert archived.state == ApiRevisionState.ARCHIVED
    assert await _count_and_rows(registry_db) == (2, 2)

    real_get_for_api = ApiRevisionRepository.get_for_api

    async def get_then_reimport(
        session: AsyncSession, api_id: uuid.UUID, revision_id: uuid.UUID
    ) -> ApiRevision | None:
        revision = await real_get_for_api(session, api_id, revision_id)
        # An identical catalog re-import commits after the delete has read the
        # archived revision and before it issues the DELETE.
        await ingestor.ingest(_spec("sha-a"), created_by="usr")
        return revision

    monkeypatch.setattr(ApiRevisionRepository, "get_for_api", staticmethod(get_then_reimport))

    with pytest.raises(RevisionNotFoundError):
        await RevisionService(ingest_context).delete(
            _IDENTIFIER.vendor,
            _IDENTIFIER.name,
            _IDENTIFIER.version,
            str(first.revision_id),
            identity=_IDENTITY,
        )

    assert await _count_and_rows(registry_db) == (2, 2)
