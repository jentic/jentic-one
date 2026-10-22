"""End to end: the Cloudflare CLI facade, through the real broker app and pipeline.

Seeds a minimal Cloudflare OpenAPI spec (imported and promoted in the
registry), an agent bound to bearer credentials with permission rules, and
mints ``cit_`` invocation tokens through the real ``CliInvocationService``.
Requests go to ``/_facade/cloudflare/…`` on the real standalone broker app;
the upstream is the broker's own ``HttpRunner`` over an ``httpx``
``MockTransport`` that records what Cloudflare would receive.

Covers: GET/POST forwarded with the stored credential and never the
``cit_``/``at_`` token; the raw path encoding and query kept; calls recorded
under the token's trace id (and grouped by ``GET /executions?trace_id=`` on
the admin surface) even when the client sends its own ``traceparent``;
response headers allowlisted; the token's credential name choosing between
two credentials; and every refusal (unregistered path, rule deny, hold,
wrong token kind or vendor, facade switched off, ambiguous credential)
answered in Cloudflare's error envelope with the pipeline's status.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.cli_invocations import CliInvocation
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentRepository,
    UserRepository,
)
from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.auth.services.cli_invocation_service import CliInvocationService
from jentic_one.auth.services.schemas.cli_invocations import CliInvocationMintPayload
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.routers import facades
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
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
    EXECUTIONS_READ,
)
from jentic_one.shared.config import CliFacadesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorStatus, ActorType, StoredCredentialType
from jentic_one.wiring import install_broker_registry_resolver

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_cf_facade_seed"
_OWNER_ID = "usr_cf_facade_owner"
_VENDOR = "cloudflare"
_API_NAME = "cloudflare"
_API_VERSION = "4.0.0"
_MAIN_SECRET = "cf-secret-main"  # pragma: allowlist secret
_OTHER_SECRET = "cf-secret-other"  # pragma: allowlist secret
_CLIENT_TRACE = "c" * 32

_SPEC = json.dumps(
    {
        "openapi": "3.0.3",
        "info": {"title": "Cloudflare API", "version": _API_VERSION},
        "servers": [{"url": "https://api.cloudflare.com/client/v4"}],
        "components": {"securitySchemes": {"api_token": {"type": "http", "scheme": "bearer"}}},
        "security": [{"api_token": []}],
        "paths": {
            "/zones": {
                "get": {"operationId": "listZones", "responses": {"200": {"description": "OK"}}},
                "post": {
                    "operationId": "createZone",
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/zones/{zone_id}": {
                "delete": {
                    "operationId": "deleteZone",
                    "parameters": [
                        {"name": "zone_id", "in": "path", "required": True, "schema": {}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/zones/{zone_id}/purge_cache": {
                "post": {
                    "operationId": "purgeCache",
                    "parameters": [
                        {"name": "zone_id", "in": "path", "required": True, "schema": {}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/{key_name}": {
                "get": {
                    "operationId": "kvGetValue",
                    "parameters": [
                        {"name": "account_id", "in": "path", "required": True, "schema": {}},
                        {"name": "namespace_id", "in": "path", "required": True, "schema": {}},
                        {"name": "key_name", "in": "path", "required": True, "schema": {}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
)


@dataclass
class _Upstream:
    """Records every request Cloudflare would receive and answers a fixed 200."""

    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.dumps({"success": True, "errors": [], "messages": [], "result": []})

        async def _content() -> AsyncIterator[bytes]:
            # An async stream, as a real upstream body: the broker streams it through.
            yield body.encode()

        return httpx.Response(
            200,
            content=_content(),
            headers={
                "Content-Type": "application/json",
                "Set-Cookie": "__cflb=upstream-session; Domain=.cloudflare.com",
                "Location": "https://api.cloudflare.com/elsewhere",
                "WWW-Authenticate": 'Bearer realm="cloudflare"',
                "cf-ray": "8a1b2c3d4e5f-LHR",
                "X-RateLimit-Remaining": "1199",
            },
        )


@dataclass
class _Env:
    ctx: Context
    broker: FastAPI
    upstream: _Upstream
    agent_id: str
    main_credential_id: str
    other_credential_id: str


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def facade_ctx(integration_context: Context) -> Iterator[Context]:
    """Cloudflare (and github) facades on; broker caches off so each call re-reads."""
    config = integration_context.config
    prior = (
        config.cli_facades,
        config.broker.resolve_cache_ttl_seconds,
        config.broker.toolkit_cache_ttl_s,
        config.broker.rule_cache_ttl_s,
    )
    config.cli_facades = CliFacadesConfig.model_validate(
        {
            "enabled": True,
            "vendors": {
                "cloudflare": {"enabled": True},
                "github": {"enabled": True, "host": "github.jentic.test"},
            },
        }
    )
    config.broker.resolve_cache_ttl_seconds = 0.0
    config.broker.toolkit_cache_ttl_s = 0
    config.broker.rule_cache_ttl_s = 0
    yield integration_context
    (
        config.cli_facades,
        config.broker.resolve_cache_ttl_seconds,
        config.broker.toolkit_cache_ttl_s,
        config.broker.rule_cache_ttl_s,
    ) = prior


async def _truncate(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
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
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(HeldExecution))
        await session.execute(delete(Job))
        await session.execute(delete(Event))
        await session.execute(delete(ExecutionRecord))
        await session.execute(delete(CliInvocation))
        await session.execute(delete(AccessToken))
        await session.execute(delete(RefreshToken))
        await session.execute(delete(AgentCredentialBinding))
        await session.execute(delete(ActorPermissionGrant))
        await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
        await session.execute(delete(User).where(User.created_by == _SEED_MARKER))
    async with ctx.control_db.session() as session:
        await session.execute(delete(AgentPermissionRule))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(Credential))
        await session.commit()


async def _seed_api(ctx: Context) -> None:
    result = await ImportHandler(ctx).execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": _SPEC,
                    "filename": "cloudflare.json",
                    "vendor": _VENDOR,
                    "api_name": _API_NAME,
                    "version": _API_VERSION,
                }
            ]
        },
        created_by=_SEED_MARKER,
    )
    revision_id = result.body["revisions"][0]["revision_id"]
    await RevisionService(ctx).promote(
        _VENDOR,
        _API_NAME,
        _API_VERSION,
        revision_id,
        identity=Identity(sub=_SEED_MARKER, permissions=["registry:approve-host-change"]),
    )


async def _seed_credential(ctx: Context, name: str, secret: str) -> str:
    credential = Credential(
        type=StoredCredentialType.STATIC_BEARER_TOKEN,
        name=name,
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
    )
    async with ctx.control_db.session() as session:
        session.add(credential)
        await session.flush()
        session.add(
            TokenValueCredential(
                credential_id=credential.id,
                encrypted_token_value=ctx.encryption.encrypt(secret),
            )
        )
        await session.commit()
        return credential.id


async def _seed_agent(ctx: Context) -> str:
    async with ctx.admin_db.transaction() as session:
        await UserRepository.create(
            session,
            id=_OWNER_ID,
            email=f"{_OWNER_ID}@cf.test",
            first_name="Cf",
            last_name="Owner",
            created_by=_SEED_MARKER,
        )
        agent = await AgentRepository.create(
            session,
            name="cf-facade-agent",
            owner_id=_OWNER_ID,
            registered_by=_OWNER_ID,
            created_by=_SEED_MARKER,
            status=ActorStatus.ACTIVE,
        )
        for permission in (BROKER_EXECUTE_PERMISSION, EXECUTIONS_READ):
            await ActorPermissionGrantRepository.grant(
                session,
                actor_id=agent.id,
                actor_type=ActorType.AGENT,
                permission=permission,
                granted_by=_OWNER_ID,
                created_by=_SEED_MARKER,
            )
        return agent.id


async def _bind(ctx: Context, agent_id: str, credential_id: str) -> None:
    """Bind the credential with: DELETE denied, purge held, GET/POST allowed."""
    async with ctx.admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=agent_id, credential_id=credential_id
            )
        )
        await session.commit()
    async with ctx.control_db.session() as session:
        for sequence, (effect, methods, path) in enumerate(
            [
                ("deny", ["DELETE"], "/zones/.*"),
                ("require-approval", ["POST"], "/zones/[^/]+/purge_cache"),
                ("allow", ["GET", "POST"], ".*"),
            ],
            start=1,
        ):
            session.add(
                AgentPermissionRule(
                    agent_id=agent_id,
                    credential_id=credential_id,
                    effect=effect,
                    methods=methods,
                    path=path,
                    sequence=sequence,
                )
            )
        await session.commit()


@pytest.fixture()
async def env(facade_ctx: Context) -> AsyncGenerator[_Env, None]:
    ctx = facade_ctx
    await _truncate(ctx)
    await _seed_api(ctx)
    main_id = await _seed_credential(ctx, "cf-main", _MAIN_SECRET)
    other_id = await _seed_credential(ctx, "cf-other", _OTHER_SECRET)
    agent_id = await _seed_agent(ctx)
    await _bind(ctx, agent_id, main_id)

    upstream = _Upstream()
    broker = create_broker_app(ctx)
    install_broker_registry_resolver(broker, ctx)
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle))
    runner = HttpRunner(mock_client)
    broker.dependency_overrides[facades._CloudflareRunner.dependency] = lambda: runner
    yield _Env(
        ctx=ctx,
        broker=broker,
        upstream=upstream,
        agent_id=agent_id,
        main_credential_id=main_id,
        other_credential_id=other_id,
    )
    await mock_client.aclose()
    await _truncate(ctx)


async def _mint(
    env: _Env, *, vendor: str = "cloudflare", credential_name: str | None = None
) -> tuple[str, str, str]:
    """Returns ``(access_token, invocation_token, trace_id)``."""
    access, _refresh = await TokenService(env.ctx).issue_pair(
        env.agent_id, ActorType.AGENT, [BROKER_EXECUTE_PERMISSION, EXECUTIONS_READ]
    )
    minted = await CliInvocationService(env.ctx).mint(
        CliInvocationMintPayload(
            vendor=vendor, credential_name=credential_name, command_title="zones list"
        ),
        identity=Identity(sub=env.agent_id, actor_type=ActorType.AGENT),
        parent_token=access,
    )
    return access, minted.token, minted.invocation.trace_id


async def _call(env: _Env, method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    async with AsyncClient(
        transport=ASGITransport(app=env.broker), base_url="http://broker.test"
    ) as client:
        return await client.request(method, f"/_facade/cloudflare{path}", headers=headers, **kwargs)


def _cloudflare_error(response: httpx.Response, status: int) -> str:
    """Assert ``response`` is Cloudflare's error envelope with ``status``; return the message."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert set(body) == {"success", "errors", "messages", "result"}
    assert body["success"] is False
    assert body["messages"] == []
    assert body["result"] is None
    (error,) = body["errors"]
    assert error["code"] == status
    message = str(error["message"])
    assert message.startswith("Jentic: ")
    return message


def _assert_no_jentic_token_upstream(request: httpx.Request, *tokens: str) -> None:
    for name, value in request.headers.items():
        assert "cit_" not in value, name
        for token in tokens:
            assert token not in value, name


async def _records(ctx: Context, trace_id: str, *, count: int = 0) -> list[ExecutionRecord]:
    """The trace's execution records, polling briefly until ``count`` have landed.

    A streamed call's record is written after its response body ends.
    """
    for _ in range(100):
        async with ctx.admin_db.session() as session:
            rows = await session.execute(
                select(ExecutionRecord).where(ExecutionRecord.trace_id == trace_id)
            )
            records = list(rows.scalars().all())
        if len(records) >= count:
            break
        await asyncio.sleep(0.05)
    return records


# ── success path ─────────────────────────────────────────────────────────────


async def test_get_and_post_reach_cloudflare_with_the_stored_credential(env: _Env) -> None:
    access, token, trace_id = await _mint(env)

    listed = await _call(
        env, "GET", "/zones?per_page=5", token, headers={"X-Debug": f"token={token}"}
    )
    created = await _call(env, "POST", "/zones", token, json={"name": "example.com"})

    assert listed.status_code == 200, listed.text
    assert listed.json()["success"] is True
    assert created.status_code == 200, created.text
    get_request, post_request = env.upstream.requests
    assert str(get_request.url) == "https://api.cloudflare.com/client/v4/zones?per_page=5"
    assert get_request.method == "GET"
    assert post_request.method == "POST"
    assert json.loads(post_request.content) == {"name": "example.com"}
    assert post_request.headers["content-type"] == "application/json"
    for request in (get_request, post_request):
        assert request.headers["authorization"] == f"Bearer {_MAIN_SECRET}"
        _assert_no_jentic_token_upstream(request, token, access)
        assert "x-debug" not in request.headers
    assert listed.headers["jentic-credential-id"] == env.main_credential_id

    records = await _records(env.ctx, trace_id, count=2)
    assert [r.http_status for r in records] == [200, 200]
    assert {r.actor_id for r in records} == {env.agent_id}
    assert {(r.operation_method, r.operation_path) for r in records} == {
        ("GET", "/zones"),
        ("POST", "/zones"),
    }


async def test_calls_group_under_the_token_trace_even_with_a_client_traceparent(
    env: _Env,
) -> None:
    access, token, trace_id = await _mint(env)
    client_traceparent = f"00-{_CLIENT_TRACE}-{'1' * 16}-01"

    for _ in range(2):
        response = await _call(
            env, "GET", "/zones", token, headers={"traceparent": client_traceparent}
        )
        assert response.status_code == 200, response.text

    assert len(await _records(env.ctx, trace_id, count=2)) == 2
    assert await _records(env.ctx, _CLIENT_TRACE) == []
    for request in env.upstream.requests:
        assert _CLIENT_TRACE not in request.headers.get("traceparent", "")

    admin = create_admin_app(env.ctx)
    async with AsyncClient(
        transport=ASGITransport(app=admin),
        base_url="http://admin.test",
        headers={"Authorization": f"Bearer {access}"},
    ) as client:
        grouped = await client.get("/executions", params={"trace_id": trace_id})
    assert grouped.status_code == 200, grouped.text
    data = grouped.json()["data"]
    assert len(data) == 2
    assert {item["trace_id"] for item in data} == {trace_id}


async def test_response_headers_are_allowlisted(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "/zones", token)

    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    assert "location" not in response.headers
    assert "www-authenticate" not in response.headers
    assert response.headers["cf-ray"] == "8a1b2c3d4e5f-LHR"
    assert response.headers["x-ratelimit-remaining"] == "1199"
    assert response.headers["jentic-execution-id"]


async def test_percent_encoded_path_reaches_cloudflare_unchanged(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(
        env, "GET", "/accounts/a1/storage/kv/namespaces/n1/values/dir%2Fkey?x=a%20b", token
    )

    assert response.status_code == 200, response.text
    (request,) = env.upstream.requests
    assert request.url.raw_path == (
        b"/client/v4/accounts/a1/storage/kv/namespaces/n1/values/dir%2Fkey?x=a%20b"
    )


async def test_credential_name_chooses_between_two_credentials(env: _Env) -> None:
    await _bind(env.ctx, env.agent_id, env.other_credential_id)
    _access, unnamed, _trace = await _mint(env)
    _access, named_other, _trace = await _mint(env, credential_name="cf-other")
    _access, named_main, _trace = await _mint(env, credential_name="cf-main")

    ambiguous = await _call(env, "GET", "/zones", unnamed)
    other = await _call(env, "GET", "/zones", named_other)
    main = await _call(env, "GET", "/zones", named_main)

    _cloudflare_error(ambiguous, 409)
    assert other.status_code == 200, other.text
    assert main.status_code == 200, main.text
    other_request, main_request = env.upstream.requests
    assert other_request.headers["authorization"] == f"Bearer {_OTHER_SECRET}"
    assert main_request.headers["authorization"] == f"Bearer {_MAIN_SECRET}"


async def test_credential_name_header_is_not_read_on_the_facade(env: _Env) -> None:
    await _bind(env.ctx, env.agent_id, env.other_credential_id)
    _access, token, _trace = await _mint(env, credential_name="cf-main")

    response = await _call(
        env, "GET", "/zones", token, headers={"Jentic-Credential-Name": "cf-other"}
    )

    assert response.status_code == 200, response.text
    (request,) = env.upstream.requests
    assert request.headers["authorization"] == f"Bearer {_MAIN_SECRET}"
    assert "jentic-credential-name" not in request.headers


# ── refusals, in Cloudflare's error envelope ─────────────────────────────────


async def test_unregistered_path_is_a_cloudflare_404(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "/user/tokens", token)

    assert "not found" in _cloudflare_error(response, 404).lower()
    assert env.upstream.requests == []


async def test_rule_deny_is_a_cloudflare_403_with_the_directive(env: _Env) -> None:
    _access, token, trace_id = await _mint(env)

    response = await _call(env, "DELETE", "/zones/z1", token)

    message = _cloudflare_error(response, 403)
    assert "denied" in message.lower()
    assert len(message) > len("Jentic: Operation denied.")
    assert env.upstream.requests == []
    async with env.ctx.admin_db.session() as session:
        events = (
            (await session.execute(select(Event).where(Event.trace_id == trace_id))).scalars().all()
        )
    assert events, "the denial is recorded under the token's trace id"


async def test_held_call_is_a_cloudflare_409_naming_the_hold(env: _Env) -> None:
    _access, token, trace_id = await _mint(env)

    response = await _call(env, "POST", "/zones/z1/purge_cache", token, json={"files": []})

    message = _cloudflare_error(response, 409)
    async with env.ctx.admin_db.session() as session:
        (held,) = (await session.execute(select(HeldExecution))).scalars().all()
    assert held.trace_id == trace_id
    assert held.id in message
    assert f"/executions/holds/{held.id}/result" in message
    assert "re-run" in message
    assert response.headers["jentic-execution-id"]
    assert env.upstream.requests == []


async def test_access_token_is_refused_with_a_cloudflare_401(env: _Env) -> None:
    access, _token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "/zones", access)

    _cloudflare_error(response, 401)
    assert env.upstream.requests == []


async def test_token_for_another_vendor_is_refused_with_a_cloudflare_401(env: _Env) -> None:
    _access, github_token, _trace_id = await _mint(env, vendor="github")

    response = await _call(env, "GET", "/zones", github_token)

    _cloudflare_error(response, 401)
    assert env.upstream.requests == []


@pytest.mark.parametrize("switch", ["master", "vendor"])
async def test_switched_off_facade_is_a_cloudflare_404(env: _Env, switch: str) -> None:
    _access, token, _trace_id = await _mint(env)
    facades_cfg = env.ctx.config.cli_facades
    if switch == "master":
        facades_cfg.enabled = False
    else:
        facades_cfg.vendors.cloudflare.enabled = False

    response = await _call(env, "GET", "/zones", token)

    _cloudflare_error(response, 404)
    assert env.upstream.requests == []


async def test_unknown_facade_paths_never_reach_the_catch_all(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    async with AsyncClient(
        transport=ASGITransport(app=env.broker),
        base_url="http://broker.test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        unknown_vendor = await client.get("/_facade/github/zones")
        bare = await client.get("/_facade")

    assert unknown_vendor.status_code == 404
    assert unknown_vendor.json()["type"] == "cli_facade_not_found"
    assert bare.status_code == 404
    assert env.upstream.requests == []
