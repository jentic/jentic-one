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
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.exc import IngestJobError
from jentic_one.registry.services.import_service import ImportHandler
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
