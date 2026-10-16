"""Integration tests: an unpinned broker 404 names a draft-only match (#1721).

Inline and URL imports land as a ``draft`` revision, and an unpinned broker call
only matches each API's live revision — so a call to a just-imported API
answered the generic ``operation_not_found`` and read like a URL bug. These
tests import a spec through the real ingest (``ImportHandler``), call the real
broker ``_handle`` over ASGI with the real registry resolver and the real
binding deriver, and assert:

- a URL that only a draft serves still answers 404 ``operation_not_found``, but
  the detail names the draft, the body carries ``draft_revision`` and an
  ``agent_directive``;
- once promoted, the same call is matched (it reaches authorization);
- a URL nothing serves keeps the generic 404 body;
- a live match never runs the draft probe, and the 404 path runs exactly one;
- a caller without ``apis:read`` gets the generic 404;
- the import job result says whether each revision was promoted.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, event, update

from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.repos import AgentRuleEvaluator, CredentialBindingResolver
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.wiring import install_broker_registry_resolver

pytestmark = pytest.mark.integration

VENDOR = "draft-hint-example"
NAME = "widgets"
VERSION = "1.0.0"
# A public IP literal: the broker's SSRF pre-check passes it without a DNS
# lookup, and no test here ever reaches the upstream.
HOST = "93.184.215.14"
WIDGETS_PATH = f"/{HOST}/v1/widgets"
UNKNOWN_PATH = f"/{HOST}/v1/nothing-here"
WRITER = "usr_draft_hint_writer"
AGENT_ID = "agnt_draft_hint"
GENERIC_DETAIL = "Operation not found — unregistered upstream URL."
# The draft probe is the only registry statement that filters on revision state.
PROBE_MARKER = "api_revisions.state ="


def _agent(*permissions: str, sub: str = AGENT_ID) -> Identity:
    return Identity(
        sub=sub,
        permissions=list(permissions or DEFAULT_AGENT_PERMISSIONS),
        actor_type=ActorType.AGENT,
    )


def _spec_source(*, origin: str | None = None) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Widgets", "version": VERSION, "description": uuid.uuid4().hex},
        "servers": [{"url": f"https://{HOST}"}],
        "paths": {
            "/v1/widgets": {
                "get": {"operationId": "listWidgets", "responses": {"200": {"description": "OK"}}}
            }
        },
    }
    source: dict[str, Any] = {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": VENDOR,
        "api_name": NAME,
        "version": VERSION,
        "submitted_by": WRITER,
    }
    if origin is not None:
        source["origin"] = origin
    return source


async def _import(ctx: Context, *, origin: str | None = None) -> dict[str, Any]:
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [_spec_source(origin=origin)]},
        created_by=WRITER,
    )
    revision: dict[str, Any] = result.body["revisions"][0]
    return revision


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


@asynccontextmanager
async def _broker(ctx: Context, identity: Identity) -> AsyncIterator[httpx.AsyncClient]:
    """The real broker ``_handle`` behind the real problem+json handlers.

    Authentication is the only stage skipped: the caller's identity is fixed
    here, so a test can choose its permissions.
    """
    app = FastAPI()
    install_broker_error_handlers(app)
    install_broker_registry_resolver(app, ctx)
    deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    evaluator = AgentRuleEvaluator(ctx.control_db)

    async with httpx.AsyncClient() as upstream:
        runner = HttpRunner(upstream)

        @app.api_route("/{upstream_url:path}", methods=["GET", "HEAD"])
        async def proxy(upstream_url: str, request: Request) -> Response:
            return await _handle(
                request, request.method, ctx, identity, deriver, evaluator, runner, None
            )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            yield client


@contextmanager
def _registry_statements(ctx: Context) -> Iterator[list[str]]:
    statements: list[str] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    sync_engine = ctx.registry_db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)


async def _promote(ctx: Context, revision_id: str) -> None:
    await RevisionService(ctx).promote(
        VENDOR,
        NAME,
        VERSION,
        revision_id,
        identity=Identity(sub=WRITER, permissions=["apis:write"]),
    )


def _pin(revision_id: str) -> str:
    return f"{VENDOR}:{NAME}:{VERSION}=rev_{uuid.UUID(revision_id).hex}"


async def test_draft_only_match_names_the_draft_in_the_404(env: Context) -> None:
    revision = await _import(env)
    revision_id = revision["revision_id"]

    async with _broker(env, _agent()) as client:
        resp = await client.get(WIDGETS_PATH)

    assert resp.status_code == 404, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "operation_not_found"
    assert body["error_origin"] == "broker"
    assert revision_id in body["detail"]
    assert f"{VENDOR}/{NAME}/{VERSION}" in body["detail"]
    assert "draft" in body["detail"]
    assert body["title"] == body["detail"]
    assert body["draft_revision"] == {
        "api": {"vendor": VENDOR, "name": NAME, "version": VERSION},
        "revision_id": revision_id,
        "state": "draft",
        "promoted": False,
        "promote_path": f"/apis/{VENDOR}/{NAME}/{VERSION}/revisions/{revision_id}:promote",
    }
    directive = body["agent_directive"]
    assert directive["strategy"] == "prompt_human"
    assert directive["parameters"]["revision_id"] == revision_id
    assert directive["parameters"]["suggested_command"] == (
        f"jentic apis promote {VENDOR}/{NAME}/{VERSION} {revision_id}"
    )
    # Not the submitter: no pin is suggested (the pin would be refused).
    assert "headers" not in directive["parameters"]


async def test_submitter_is_offered_the_revision_pin(env: Context) -> None:
    revision = await _import(env)
    revision_id = revision["revision_id"]

    async with _broker(env, _agent(sub=WRITER)) as client:
        resp = await client.get(WIDGETS_PATH)

    assert resp.status_code == 404, resp.text
    directive = resp.json()["agent_directive"]
    assert directive["strategy"] == "modify_headers"
    pin = directive["parameters"]["headers"]
    assert pin == {"Jentic-Revision": _pin(revision_id)}

    # The suggested pin actually routes the call through the draft: it is
    # matched, and only authorization (no binding) stops it.
    async with _broker(env, _agent(sub=WRITER)) as client:
        pinned = await client.get(WIDGETS_PATH, headers=pin)
    assert pinned.status_code == 403, pinned.text
    assert pinned.json()["type"] == "no_credential_binding"


async def test_head_falls_back_to_the_draft_get(env: Context) -> None:
    revision = await _import(env)

    async with _broker(env, _agent()) as client:
        resp = await client.get(WIDGETS_PATH)
        head = await client.head(WIDGETS_PATH)

    assert resp.status_code == 404
    assert head.status_code == 404
    # A HEAD response has no body; the hint is asserted on the GET above.
    assert resp.json()["draft_revision"]["revision_id"] == revision["revision_id"]


async def test_promoted_revision_is_matched(env: Context) -> None:
    revision = await _import(env)
    await _promote(env, revision["revision_id"])

    async with _broker(env, _agent()) as client:
        resp = await client.get(WIDGETS_PATH)

    # Matched: discovery resolved the operation, so the call moved on to
    # authorization, which refuses it because this agent holds no binding.
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "no_credential_binding"


async def test_no_match_keeps_the_generic_404(env: Context) -> None:
    await _import(env)

    async with _broker(env, _agent()) as client:
        resp = await client.get(UNKNOWN_PATH)

    assert resp.status_code == 404
    body = resp.json()
    assert body["type"] == "operation_not_found"
    assert body["detail"] == GENERIC_DETAIL
    assert "draft_revision" not in body
    assert "agent_directive" not in body


async def test_live_match_never_runs_the_draft_probe(env: Context) -> None:
    revision = await _import(env)
    await _promote(env, revision["revision_id"])

    async with _broker(env, _agent()) as client:
        with _registry_statements(env) as statements:
            resp = await client.get(WIDGETS_PATH)

    assert resp.status_code == 403, resp.text
    assert not [s for s in statements if PROBE_MARKER in s], statements


async def test_404_path_runs_exactly_one_draft_probe(env: Context) -> None:
    await _import(env)

    async with _broker(env, _agent()) as client:
        with _registry_statements(env) as statements:
            resp = await client.get(WIDGETS_PATH)

    assert resp.status_code == 404
    # The live lookup (exact host, then the regex-host fallback) plus one probe.
    assert len(statements) == 3, statements
    assert len([s for s in statements if PROBE_MARKER in s]) == 1, statements


async def test_caller_without_apis_read_gets_the_generic_404(env: Context) -> None:
    await _import(env)

    async with _broker(env, _agent("executions:read")) as client:
        with _registry_statements(env) as statements:
            resp = await client.get(WIDGETS_PATH)

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == GENERIC_DETAIL
    assert "draft_revision" not in body
    assert "agent_directive" not in body
    # No visibility, no probe: nothing about drafts is even read.
    assert not [s for s in statements if PROBE_MARKER in s], statements


async def test_import_result_flags_an_unpromoted_draft(env: Context) -> None:
    revision = await _import(env)

    assert revision["state"] == "draft"
    assert revision["promoted"] is False
    assert revision["promote_path"] == (
        f"/apis/{VENDOR}/{NAME}/{VERSION}/revisions/{revision['revision_id']}:promote"
    )
    assert "promote" in revision["promote_hint"]


async def test_import_result_flags_an_auto_promoted_revision(env: Context) -> None:
    revision = await _import(env, origin="catalog")

    assert revision["state"] == "imported"
    assert revision["promoted"] is True
    assert "promote_path" not in revision
    assert "promote_hint" not in revision
