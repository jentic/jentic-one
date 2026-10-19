"""Integration tests: an unpinned broker 404 names a draft-only match (#1721).

Pins #1721: inline and URL imports land as a ``draft`` revision and unpinned
discovery only matches each API's live revision, so a 404 for a URL that only
a draft serves must name that draft — but only to a caller who submitted it or
may pin it. These tests import specs through the real ingest
(``ImportHandler``) and call the real broker execute route over ASGI, behind
real broker authentication (opaque tokens resolved from the admin DB), with
the real registry resolver and binding deriver. They assert:

- the submitter of a draft that serves the URL gets a 404
  ``operation_not_found`` naming the draft, with ``draft_revision`` and a
  ``modify_headers`` directive whose pin routes the call;
- another principal holding only ``capabilities:execute`` gets the generic
  404: the draft is not disclosed;
- a submitter whose draft needs an operator gets ``prompt_human``, no pin;
- a pinned call that misses keeps the request's other pins in the suggestion;
- once promoted, the same call is matched (it reaches authorization);
- a URL nothing serves keeps the generic 404 body;
- a live match never runs the draft probe, and the 404 path runs exactly one;
- the draft hint's pin outcome is the registry's own pin rule, across the
  submitter / operator / held-draft matrix;
- the import job result says whether each revision was promoted.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, event, select, update

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.broker.web.deps import get_http_runner
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import router as execute_router
from jentic_one.control.core.schema.credentials import Credential
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
from jentic_one.shared.auth.permission_catalog import (
    BROKER_EXECUTE_PERMISSION,
    CREDENTIALS_WRITE,
)
from jentic_one.shared.broker.protocols import RevisionPinOutcome
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver

pytestmark = pytest.mark.integration

VENDOR = "draft-hint-example"
NAME = "widgets"
VERSION = "1.0.0"
# Public IP literals: the broker's SSRF pre-check passes them without a DNS
# lookup, and no test here ever reaches the upstream.
HOST = "93.184.215.14"
OLD_HOST = "93.184.215.15"
WIDGETS_PATH = f"/{HOST}/v1/widgets"
UNKNOWN_PATH = f"/{HOST}/v1/nothing-here"
SUBMITTER = "agnt_draft_hint_submitter"
OTHER = "agnt_draft_hint_other"
OPERATOR = "agnt_draft_hint_operator"
SEED_MARKER = "usr_draft_hint_seed"
BOUND_AGENT_NAME = "draft-hint-bound-agent"
GENERIC_DETAIL = "Operation not found — unregistered upstream URL."
# The draft probe is the only registry statement that filters on revision state.
PROBE_MARKER = "api_revisions.state ="


def _spec_source(
    *, host: str = HOST, origin: str | None = None, marker: str | None = None
) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Widgets",
            "version": VERSION,
            "description": marker or uuid.uuid4().hex,
        },
        "servers": [{"url": f"https://{host}"}],
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
        "submitted_by": SUBMITTER,
    }
    if origin is not None:
        source["origin"] = origin
    return source


async def _import(ctx: Context, *, host: str = HOST, origin: str | None = None) -> dict[str, Any]:
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        db=None,
        payload={"sources": [_spec_source(host=host, origin=origin)]},
        created_by=SUBMITTER,
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
    async with ctx.control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.api_vendor == VENDOR))
        await session.commit()
    async with ctx.admin_db.session() as session:
        bound = select(Agent.id).where(Agent.name == BOUND_AGENT_NAME)
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(bound))
        )
        await session.execute(delete(AccessToken).where(AccessToken.created_by == SEED_MARKER))
        await session.execute(
            delete(Agent).where(
                (Agent.created_by == SEED_MARKER) | (Agent.name == BOUND_AGENT_NAME)
            )
        )
        await session.commit()


@pytest.fixture()
async def env(integration_context: Context) -> AsyncGenerator[Context, None]:
    await _wipe(integration_context)
    yield integration_context
    await _wipe(integration_context)


async def _token(ctx: Context, actor_id: str, *permissions: str) -> dict[str, str]:
    """Seed an active agent plus an opaque access token; return its auth header.

    The token carries ``capabilities:execute`` (what the broker route requires)
    plus ``permissions``, so the broker resolves the caller exactly as in
    production.
    """
    plaintext = f"at_{uuid.uuid4().hex}"
    async with ctx.admin_db.session() as session:
        session.add(
            Agent(
                id=actor_id,
                name=f"draft-hint-{actor_id}",
                registered_by=SEED_MARKER,
                created_by=SEED_MARKER,
                status="active",
            )
        )
        await session.flush()
        await AccessTokenRepository.create(
            session,
            token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            actor_id=actor_id,
            actor_type=ActorType.AGENT.value,
            scopes=[BROKER_EXECUTE_PERMISSION, *permissions],
            token_family_id=f"fam_{uuid.uuid4().hex[:12]}",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by=SEED_MARKER,
            is_ephemeral=True,
        )
        await session.commit()
    return {"Authorization": f"Bearer {plaintext}"}


async def _bind_credential(ctx: Context) -> None:
    """A credential covering the API, bound to an agent: host changes now need an operator."""
    credential_id = generate_ksuid("cred")
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=credential_id,
                type=StoredCredentialType.API_KEY,
                name="cred-draft-hint",
                api_vendor=VENDOR,
                api_name=NAME,
                api_version=VERSION,
                created_by=SEED_MARKER,
            )
        )
        await session.commit()
    async with ctx.admin_db.session() as session:
        agent = Agent(name=BOUND_AGENT_NAME, registered_by=SEED_MARKER)
        session.add(agent)
        await session.flush()
        session.add(
            AgentCredentialBinding(agent_id=agent.id, credential_id=credential_id, suspended=True)
        )
        await session.commit()


@asynccontextmanager
async def _broker(ctx: Context) -> AsyncIterator[httpx.AsyncClient]:
    """The real broker execute route, behind real broker auth and problem+json handlers.

    Only the upstream runner is supplied directly (``get_http_runner`` is the
    documented test seam); no test here reaches the upstream.
    """
    app = FastAPI()
    app.state.ctx = ctx
    install_broker_auth(app, ctx)
    install_broker_error_handlers(app)
    install_broker_registry_resolver(app, ctx)
    app.include_router(execute_router)

    async with httpx.AsyncClient() as upstream:
        runner = HttpRunner(upstream)
        app.dependency_overrides[get_http_runner] = lambda: runner
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
        identity=Identity(sub=SUBMITTER, permissions=["apis:write"]),
    )


def _pin(revision_id: str) -> str:
    return f"{VENDOR}:{NAME}:{VERSION}=rev_{uuid.UUID(revision_id).hex}"


async def test_submitter_is_told_about_its_draft_and_offered_the_pin(env: Context) -> None:
    revision = await _import(env)
    revision_id = revision["revision_id"]
    auth = await _token(env, SUBMITTER)

    async with _broker(env) as client:
        resp = await client.get(WIDGETS_PATH, headers=auth)

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
    assert directive["strategy"] == "modify_headers"
    assert directive["parameters"]["revision_id"] == revision_id
    assert directive["parameters"]["suggested_command"] == (
        f"jentic apis promote {VENDOR}/{NAME}/{VERSION} {revision_id}"
    )
    pin = directive["parameters"]["headers"]
    assert pin == {"Jentic-Revision": _pin(revision_id)}

    # The suggested pin routes the call through the draft: it is matched, and
    # only authorization (no binding) stops it.
    async with _broker(env) as client:
        pinned = await client.get(WIDGETS_PATH, headers={**auth, **pin})
    assert pinned.status_code == 403, pinned.text
    assert pinned.json()["type"] == "no_credential_binding"


async def test_another_principals_draft_is_not_disclosed(env: Context) -> None:
    """A caller holding only ``capabilities:execute`` gets the generic 404."""
    await _import(env)
    auth = await _token(env, OTHER)

    async with _broker(env) as client:
        with _registry_statements(env) as statements:
            resp = await client.get(WIDGETS_PATH, headers=auth)

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["type"] == "operation_not_found"
    assert body["detail"] == GENERIC_DETAIL
    assert "draft_revision" not in body
    assert "agent_directive" not in body
    # The live lookup (exact host, then the regex-host fallback) plus one probe.
    assert len(statements) == 3, statements
    assert len([s for s in statements if PROBE_MARKER in s]) == 1, statements


async def test_operator_does_not_see_another_principals_plain_draft(env: Context) -> None:
    """``credentials:write`` pins only drafts held for host review, so it reveals no others."""
    await _import(env)
    auth = await _token(env, OPERATOR, CREDENTIALS_WRITE)

    async with _broker(env) as client:
        resp = await client.get(WIDGETS_PATH, headers=auth)

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == GENERIC_DETAIL


async def test_submitter_draft_held_for_an_operator_asks_for_a_human(env: Context) -> None:
    """The submitter's draft moves a credential-bound API to new hosts: no pin is offered."""
    await _import(env, host=OLD_HOST, origin="catalog")
    await _bind_credential(env)
    revision = await _import(env)
    assert revision["state"] == "draft"
    auth = await _token(env, SUBMITTER)

    async with _broker(env) as client:
        resp = await client.get(WIDGETS_PATH, headers=auth)

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["draft_revision"]["revision_id"] == revision["revision_id"]
    directive = body["agent_directive"]
    assert directive["strategy"] == "prompt_human"
    assert "headers" not in directive["parameters"]


async def test_pinned_miss_keeps_the_requests_other_pins(env: Context) -> None:
    revision = await _import(env)
    auth = await _token(env, SUBMITTER)
    other_pin = f"other-vendor:things:2.0.0=rev_{uuid.uuid4().hex}"

    async with _broker(env) as client:
        resp = await client.get(WIDGETS_PATH, headers={**auth, "Jentic-Revision": other_pin})

    assert resp.status_code == 404, resp.text
    directive = resp.json()["agent_directive"]
    assert directive["strategy"] == "modify_headers"
    assert directive["parameters"]["headers"] == {
        "Jentic-Revision": f"{other_pin}, {_pin(revision['revision_id'])}"
    }


async def test_head_falls_back_to_the_draft_get(env: Context) -> None:
    revision = await _import(env)
    auth = await _token(env, SUBMITTER)

    async with _broker(env) as client:
        resp = await client.get(WIDGETS_PATH, headers=auth)
        head = await client.head(WIDGETS_PATH, headers=auth)

    assert resp.status_code == 404
    assert head.status_code == 404
    # A HEAD response has no body; the hint is asserted on the GET above.
    assert resp.json()["draft_revision"]["revision_id"] == revision["revision_id"]


async def test_promoted_revision_is_matched(env: Context) -> None:
    revision = await _import(env)
    await _promote(env, revision["revision_id"])
    auth = await _token(env, OTHER)

    async with _broker(env) as client:
        resp = await client.get(WIDGETS_PATH, headers=auth)

    # Matched: discovery resolved the operation, so the call moved on to
    # authorization, which refuses it because this agent holds no binding.
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "no_credential_binding"


async def test_no_match_keeps_the_generic_404(env: Context) -> None:
    await _import(env)
    auth = await _token(env, SUBMITTER)

    async with _broker(env) as client:
        resp = await client.get(UNKNOWN_PATH, headers=auth)

    assert resp.status_code == 404
    body = resp.json()
    assert body["type"] == "operation_not_found"
    assert body["detail"] == GENERIC_DETAIL
    assert "draft_revision" not in body
    assert "agent_directive" not in body


async def test_live_match_never_runs_the_draft_probe(env: Context) -> None:
    revision = await _import(env)
    await _promote(env, revision["revision_id"])
    auth = await _token(env, OTHER)

    async with _broker(env) as client:
        with _registry_statements(env) as statements:
            resp = await client.get(WIDGETS_PATH, headers=auth)

    assert resp.status_code == 403, resp.text
    assert not [s for s in statements if PROBE_MARKER in s], statements


def _identity(sub: str, *permissions: str) -> Identity:
    return Identity(
        sub=sub,
        actor_type=ActorType.AGENT,
        permissions=[BROKER_EXECUTE_PERMISSION, *permissions],
    )


@pytest.mark.parametrize(
    ("draft", "identity", "expected"),
    [
        ("plain", _identity(SUBMITTER), RevisionPinOutcome.RESOLVED),
        ("plain", _identity(OTHER), None),
        ("plain", _identity(OPERATOR, CREDENTIALS_WRITE), None),
        ("held_catalog", _identity(SUBMITTER), RevisionPinOutcome.FORBIDDEN),
        ("held_catalog", _identity(OTHER), None),
        ("held_catalog", _identity(OPERATOR, CREDENTIALS_WRITE), RevisionPinOutcome.RESOLVED),
        ("host_change", _identity(SUBMITTER), RevisionPinOutcome.HOST_CHANGE_HELD),
        ("host_change", _identity(SUBMITTER, CREDENTIALS_WRITE), RevisionPinOutcome.RESOLVED),
        ("host_change", _identity(OPERATOR, CREDENTIALS_WRITE), None),
    ],
)
async def test_hint_pin_outcome_is_the_registry_pin_rule(
    env: Context, draft: str, identity: Identity, expected: RevisionPinOutcome | None
) -> None:
    """``find_draft_match`` names a draft only to its submitter or a caller who may
    pin it, and its pin outcome is what ``resolve_revision_pin`` answers for the
    same caller — so the hint never offers a pin the broker then refuses.

    ``plain``: an inline draft, nothing live. ``held_catalog``: a catalog
    re-import held for server-host review. ``host_change``: the submitter's
    inline draft moving a credential-bound API to new hosts.
    """
    if draft == "plain":
        revision = await _import(env)
    else:
        await _import(env, host=OLD_HOST, origin="catalog")
        await _bind_credential(env)
        revision = await _import(env, origin="catalog" if draft == "held_catalog" else None)
    assert revision["state"] == "draft"
    revision_id = uuid.UUID(revision["revision_id"])
    resolver = InProcessRegistryResolver(env.registry_db, ctx=env)

    match = await resolver.find_draft_match(
        method="GET", url=f"https://{HOST}/v1/widgets", identity=identity
    )
    pin = await resolver.resolve_revision_pin(
        vendor=VENDOR,
        name=NAME,
        version=VERSION,
        rev_label=f"rev_{revision_id.hex}",
        identity=identity,
    )

    if expected is None:
        assert match is None
        assert pin.outcome is RevisionPinOutcome.FORBIDDEN
    else:
        assert match is not None
        assert match.revision_id == revision_id
        assert match.pin_outcome is expected
        assert pin.outcome is expected


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
