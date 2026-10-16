"""Integration tests for the draft-candidate probe behind the draft-only 404 hint (#1721).

``URLLookupService.find_draft_candidates`` reads draft URL-index rows in one
capped statement. These pin its precedence and its overflow rule:

- a revision served by an exact-host row ranks before one served only by a
  regex-host row, even when the regex-host draft is newer;
- more matching rows than the cap yields no candidates (the generic 404),
  never a truncated, possibly wrong, ranking;
- only drafts the caller may be told about are read, so other principals'
  drafts neither appear nor count toward the cap.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.inspect import url_lookup
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration

VENDOR = "draft-candidates"
URL = "https://api.draft-candidates.example/v1/widgets"
SUBMITTER = "agnt_draft_candidates"


def _source(name: str, server_url: str) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": name, "version": "1.0.0"},
        "servers": [{"url": server_url}],
        "paths": {
            "/v1/widgets": {
                "get": {"operationId": "listWidgets", "responses": {"200": {"description": "OK"}}}
            }
        },
    }
    return {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": VENDOR,
        "api_name": name,
        "version": "1.0.0",
        "submitted_by": SUBMITTER,
    }


async def _import_draft(ctx: Context, source: dict[str, Any]) -> uuid.UUID:
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [source]},
        created_by="agnt_draft_candidates",
    )
    revision = result.body["revisions"][0]
    assert revision["state"] == "draft"
    return uuid.UUID(revision["revision_id"])


async def _wipe(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        for table in (
            OperationURLIndex,
            SecuritySchemeFlow,
            SecurityScheme,
            ServerVariable,
            Server,
            Operation,
            SpecFile,
        ):
            await session.execute(delete(table))
        await session.execute(update(Api).values(current_revision_id=None))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.commit()


@pytest.fixture()
async def env(integration_context: Context) -> AsyncGenerator[Context, None]:
    await _wipe(integration_context)
    yield integration_context
    await _wipe(integration_context)


async def _candidates(
    ctx: Context, *, submitted_by: str = SUBMITTER, include_origin_drafts: bool = False
) -> list[uuid.UUID]:
    async with ctx.registry_db.session() as session:
        found = await URLLookupService(session).find_draft_candidates(
            method="GET",
            url=URL,
            submitted_by=submitted_by,
            include_origin_drafts=include_origin_drafts,
        )
    return [candidate.row.revision_id for candidate in found]


async def _set_revision(ctx: Context, revision_id: uuid.UUID, **values: Any) -> None:
    async with ctx.registry_db.session() as session:
        await session.execute(
            update(ApiRevision).where(ApiRevision.id == revision_id).values(**values)
        )
        await session.commit()


async def test_exact_host_draft_ranks_before_a_newer_regex_host_draft(env: Context) -> None:
    exact = await _import_draft(env, _source("exact", "https://api.draft-candidates.example"))
    regex = await _import_draft(env, _source("regex", "https://api.draft-candidates.example"))
    # Make the newer draft regex-host only (``host IS NULL``), as a host that
    # cannot be indexed literally is.
    async with env.registry_db.session() as session:
        await session.execute(
            update(OperationURLIndex)
            .where(OperationURLIndex.revision_id == regex)
            .values(host=None, host_regex=r"^[a-z]+\.draft\-candidates\.example$")
        )
        await session.commit()

    assert await _candidates(env) == [exact, regex]


async def test_more_rows_than_the_cap_yield_no_candidates(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _import_draft(env, _source("first", "https://api.draft-candidates.example"))
    await _import_draft(env, _source("second", "https://api.draft-candidates.example"))
    assert len(await _candidates(env)) == 2

    monkeypatch.setattr(url_lookup, "MAX_CANDIDATES", 1)

    assert await _candidates(env) == []


async def test_other_principals_drafts_do_not_count_toward_the_cap(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = await _import_draft(env, _source("own", "https://api.draft-candidates.example"))
    others = await _import_draft(env, _source("others", "https://api.draft-candidates.example"))
    await _set_revision(env, others, submitted_by="agnt_someone_else")

    monkeypatch.setattr(url_lookup, "MAX_CANDIDATES", 1)

    # Two matching drafts exceed the cap of 1, but only the submitter's own is
    # read, so its hint survives and the other draft's size reveals nothing.
    assert await _candidates(env) == [own]
    assert await _candidates(env, submitted_by="agnt_someone_else") == [others]
    assert await _candidates(env, submitted_by="agnt_stranger") == []


async def test_drafts_held_for_host_review_are_read_only_for_operators(env: Context) -> None:
    held = await _import_draft(env, _source("held", "https://api.draft-candidates.example"))
    await _set_revision(env, held, submitted_by="agnt_someone_else", origin="url_import")

    assert await _candidates(env) == []
    assert await _candidates(env, include_origin_drafts=True) == [held]
