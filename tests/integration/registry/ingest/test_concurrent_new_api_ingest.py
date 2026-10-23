"""Integration test: concurrent ingests of one brand-new API identity all succeed.

Pins #1980: imports racing to create the same new ``(vendor, name, version)``
must converge on a single ``apis`` row instead of the losers failing
``ResolveApiStage`` on ``uq_apis_vendor_name_version``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType

pytestmark = pytest.mark.integration

_CONCURRENCY = 8
_IDENTIFIER = ApiIdentifier(vendor="concurrent.example.com", name="new-api", version="1.0.0")


def _content(n: int) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": f"Concurrent API {n}", "version": "1.0.0"},
        "servers": [{"url": "https://api.concurrent.example.com"}],
        "paths": {
            f"/ping{n}": {
                "get": {"operationId": f"ping{n}", "responses": {"200": {"description": "ok"}}}
            }
        },
    }


def _spec(n: int) -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=_IDENTIFIER,
        sha=f"sha-concurrent-{n}",
        content=_content(n),
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
    )


async def test_concurrent_ingests_of_new_identity_share_one_api_row(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    results = await asyncio.gather(
        *(Ingestor(ingest_context).ingest(_spec(n), created_by="usr") for n in range(_CONCURRENCY)),
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    assert failures == []

    async with registry_db.session() as session:
        apis = list(
            (
                await session.execute(
                    select(Api).where(
                        Api.vendor == _IDENTIFIER.vendor,
                        Api.name == _IDENTIFIER.name,
                        Api.version == _IDENTIFIER.version,
                    )
                )
            ).scalars()
        )
        assert len(apis) == 1
        rows = (
            await session.execute(
                select(func.count())
                .select_from(ApiRevision)
                .where(ApiRevision.api_id == apis[0].id)
            )
        ).scalar_one()
    assert rows == _CONCURRENCY
    assert apis[0].revision_count == _CONCURRENCY
