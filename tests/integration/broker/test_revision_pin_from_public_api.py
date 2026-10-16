"""Integration tests: a ``Jentic-Revision`` pin built from public API responses (#1720).

Every public surface (the registry revision list/get, the import job result,
``jentic apis revisions``) names a revision by its ``revision_id`` UUID, so the
broker accepts ``vendor:name:version=<revision_id>`` as a pin alongside the
``rev_<hex>`` label. These tests import a spec through the real ingest
(``ImportHandler``), read the revision back through the real registry router,
build the pin from that response alone, and call the real broker ``_handle`` over
ASGI with the real registry resolver and binding deriver. They assert:

- the revision a URL is served only by is unreachable unpinned (404) and is
  matched once pinned by its listed ``revision_id`` (it reaches authorization);
- the ``revision_id`` from ``GET .../revisions/{revision_id}`` and from the import
  job result pins the same way;
- the ``rev_<hex>`` label still pins (backward compatibility);
- a pinned revision wins over the live one for a URL both serve, and the pin
  resolves to exactly the listed revision.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, update

from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.revisions import parse_revisions
from jentic_one.broker.repos import AgentRuleEvaluator, CredentialBindingResolver
from jentic_one.broker.services.discovery import discover, resolve_pin_for_api
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
from jentic_one.registry.web.app import create_app as create_registry_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver

pytestmark = pytest.mark.integration

VENDOR = "public-pin-example"
NAME = "widgets"
VERSION = "1.0.0"
# A public IP literal: the broker's SSRF pre-check passes it without a DNS
# lookup, and no test here ever reaches the upstream.
HOST = "93.184.215.14"
WIDGETS_PATH = f"/{HOST}/v1/widgets"
GADGETS_PATH = f"/{HOST}/v1/gadgets"
WRITER = "usr_public_pin_writer"
REVISIONS_URL = f"/apis/{VENDOR}/{NAME}/{VERSION}/revisions"


def _agent() -> Identity:
    """The agent that submitted the drafts (a draft pin is owner-only)."""
    return Identity(
        sub=WRITER, permissions=list(DEFAULT_AGENT_PERMISSIONS), actor_type=ActorType.AGENT
    )


def _spec_source(paths: list[str]) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Widgets", "version": VERSION, "description": uuid.uuid4().hex},
        "servers": [{"url": f"https://{HOST}"}],
        "paths": {
            path: {
                "get": {
                    "operationId": f"get{path.rsplit('/', 1)[-1].title()}",
                    "responses": {"200": {"description": "OK"}},
                }
            }
            for path in paths
        },
    }
    return {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": VENDOR,
        "api_name": NAME,
        "version": VERSION,
        "submitted_by": WRITER,
    }


async def _import(ctx: Context, paths: list[str]) -> dict[str, Any]:
    """Import a spec as a draft and return its import-job result entry."""
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [_spec_source(paths)]},
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
async def _registry(ctx: Context) -> AsyncIterator[httpx.AsyncClient]:
    """The real registry app; only authentication is fixed to the writer."""
    app = create_registry_app(ctx)
    app.dependency_overrides[resolve_identity] = lambda: Identity(
        sub=WRITER, permissions=["apis:read", "apis:write"]
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://registry") as client:
        yield client
    app.dependency_overrides.clear()


@asynccontextmanager
async def _broker(ctx: Context) -> AsyncIterator[httpx.AsyncClient]:
    """The real broker ``_handle`` behind the real problem+json handlers.

    Authentication is the only stage skipped: the caller is fixed to the agent
    that submitted the drafts.
    """
    app = FastAPI()
    install_broker_error_handlers(app)
    install_broker_registry_resolver(app, ctx)
    deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    evaluator = AgentRuleEvaluator(ctx.control_db)
    identity = _agent()

    async with httpx.AsyncClient() as upstream:
        runner = HttpRunner(upstream)

        @app.api_route("/{upstream_url:path}", methods=["GET"])
        async def proxy(upstream_url: str, request: Request) -> Response:
            return await _handle(
                request, request.method, ctx, identity, deriver, evaluator, runner, None
            )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            yield client


async def _listed_revision_ids(ctx: Context, *, state: str) -> list[str]:
    """``revision_id`` of every listed revision in ``state``, read over the public API."""
    async with _registry(ctx) as registry:
        resp = await registry.get(REVISIONS_URL, params={"state": state})
    assert resp.status_code == 200, resp.text
    return [item["revision_id"] for item in resp.json()["data"]]


def _pin(revision_id: str) -> dict[str, str]:
    """A ``Jentic-Revision`` header built from a public ``revision_id`` alone."""
    return {"Jentic-Revision": f"{VENDOR}:{NAME}:{VERSION}={revision_id}"}


def _assert_matched(resp: httpx.Response) -> None:
    """The call resolved to an operation: only the missing binding stops it."""
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "no_credential_binding"


async def test_listed_revision_id_pins_a_draft_only_url(env: Context) -> None:
    await _import(env, ["/v1/widgets"])
    (revision_id,) = await _listed_revision_ids(env, state="draft")

    async with _broker(env) as broker:
        unpinned = await broker.get(WIDGETS_PATH)
        pinned = await broker.get(WIDGETS_PATH, headers=_pin(revision_id))

    assert unpinned.status_code == 404, unpinned.text
    assert unpinned.json()["type"] == "operation_not_found"
    _assert_matched(pinned)


async def test_revision_id_from_get_and_import_result_pins(env: Context) -> None:
    imported = await _import(env, ["/v1/widgets"])
    async with _registry(env) as registry:
        got = await registry.get(f"{REVISIONS_URL}/{imported['revision_id']}")
    assert got.status_code == 200, got.text

    async with _broker(env) as broker:
        from_get = await broker.get(WIDGETS_PATH, headers=_pin(got.json()["revision_id"]))
        from_job = await broker.get(WIDGETS_PATH, headers=_pin(imported["revision_id"]))

    _assert_matched(from_get)
    _assert_matched(from_job)


async def test_rev_hex_label_still_pins(env: Context) -> None:
    await _import(env, ["/v1/widgets"])
    (revision_id,) = await _listed_revision_ids(env, state="draft")
    label = f"rev_{uuid.UUID(revision_id).hex}"

    async with _broker(env) as broker:
        pinned = await broker.get(
            WIDGETS_PATH, headers={"Jentic-Revision": f"{VENDOR}:{NAME}:{VERSION}={label}"}
        )

    _assert_matched(pinned)


async def test_listed_revision_id_pins_over_the_live_revision(env: Context) -> None:
    live = await _import(env, ["/v1/widgets"])
    await RevisionService(env).promote(
        VENDOR,
        NAME,
        VERSION,
        live["revision_id"],
        identity=Identity(sub=WRITER, permissions=["apis:write"]),
    )
    await _import(env, ["/v1/widgets", "/v1/gadgets"])
    (draft_id,) = await _listed_revision_ids(env, state="draft")
    assert draft_id != live["revision_id"]

    async with _broker(env) as broker:
        unpinned = await broker.get(GADGETS_PATH)
        pinned = await broker.get(GADGETS_PATH, headers=_pin(draft_id))
    assert unpinned.status_code == 404, unpinned.text
    _assert_matched(pinned)

    # The pin resolves to exactly the listed draft, and a URL both revisions
    # serve answers from the draft when pinned and from the live one unpinned.
    resolver = InProcessRegistryResolver(env.registry_db, ctx=env)
    pins = parse_revisions(_pin(draft_id)["Jentic-Revision"])
    pinned_id = await resolve_pin_for_api(
        resolver,
        api=APIReference(vendor=VENDOR, name=NAME, version=VERSION),
        pins=pins,
        identity=_agent(),
    )
    assert pinned_id == uuid.UUID(draft_id)

    widgets_url = f"https://{HOST}/v1/widgets"
    live_hit = await discover(resolver, method="GET", url=widgets_url)
    draft_hit = await discover(resolver, method="GET", url=widgets_url, revision_id=pinned_id)
    assert live_hit is not None
    assert draft_hit is not None
    assert live_hit.operation.id != draft_hit.operation.id
