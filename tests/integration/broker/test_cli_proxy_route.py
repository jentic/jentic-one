"""End to end: the broker's CLI proxy route, through the real broker app and pipeline.

``/_facade/proxy/<full upstream URL>`` is the broker end of an egress proxy
that relays a vendor CLI's calls with the command's ``cit_`` invocation token.
Seeds a GitHub API (vendor ``github.com``) and a Cloudflare API (vendor
``cloudflare.com``), an agent bound to two GitHub bearer credentials with an
allow rule, and mints invocation tokens through the real
``CliInvocationService``. The upstream is the broker's own ``HttpRunner`` over
an ``httpx`` ``MockTransport`` that records what the vendor would receive.

Covers: the stored credential injected and never the ``cit_``/``at_`` token;
the raw path encoding and query kept; GitHub's response headers (``Link``,
``X-GitHub-*``) kept for a GitHub URL; calls recorded under the token's trace
id (and grouped by ``GET /executions?trace_id=``) even with a client
``traceparent``; the token's credential name choosing the credential; and the
refusals, each in the error shape of the URL's vendor (GitHub's for
``*.github.com``, Cloudflare's for ``*.cloudflare.com``, standard Problem
Details otherwise): an ``at_`` token (401), a URL of another vendor's API
(403), an unregistered URL (404) and a switched-off vendor (404).
"""

from __future__ import annotations

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
from jentic_one.broker.web.deps import get_cli_proxy_runner
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
from jentic_one.shared.models.api_identity import slugify_api_field
from jentic_one.wiring import install_broker_registry_resolver

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_cli_proxy_seed"
_OWNER_ID = "usr_cli_proxy_owner"
_GITHUB_VENDOR = "github.com"
_GITHUB_NAME = "rest"
_CLOUDFLARE_VENDOR = "cloudflare.com"
_CLOUDFLARE_NAME = "api"
_VERSION = "1.0.0"
_MAIN_SECRET = "gh-proxy-secret-main"  # pragma: allowlist secret
_OTHER_SECRET = "gh-proxy-secret-other"  # pragma: allowlist secret
_CLIENT_TRACE = "d" * 32
_PROBLEM_JSON = "application/problem+json"


def _spec(title: str, server: str, paths: dict[str, Any]) -> str:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": title, "version": _VERSION},
            "servers": [{"url": server}],
            "components": {"securitySchemes": {"token": {"type": "http", "scheme": "bearer"}}},
            "security": [{"token": []}],
            "paths": paths,
        }
    )


def _op(operation_id: str, *params: str) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "parameters": [
            {"name": name, "in": "path", "required": True, "schema": {}} for name in params
        ],
        "responses": {"200": {"description": "OK"}},
    }


_GITHUB_SPEC = _spec(
    "GitHub REST",
    "https://api.github.com",
    {
        "/user": {"get": _op("getUser")},
        "/repos/{owner}/{repo}/contents/{path}": {
            "get": _op("getContent", "owner", "repo", "path")
        },
        "/repos/{owner}/{repo}/issues": {"post": _op("createIssue", "owner", "repo")},
    },
)

_CLOUDFLARE_SPEC = _spec(
    "Cloudflare", "https://api.cloudflare.com/client/v4", {"/zones": {"get": _op("listZones")}}
)


@dataclass
class _Upstream:
    """Records every request a vendor would receive and answers a fixed 200."""

    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        async def _content() -> AsyncIterator[bytes]:
            # An async stream, as a real upstream body: the broker streams it through.
            yield b'{"ok": true}'

        return httpx.Response(
            200,
            content=_content(),
            headers={
                "Content-Type": "application/json",
                "Set-Cookie": "_gh_sess=upstream-session; Domain=.github.com",
                "X-RateLimit-Remaining": "4999",
                "Link": '<https://api.github.com/user/repos?page=2>; rel="next"',
                "X-GitHub-Request-Id": "ABCD:1234",
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
def proxy_ctx(integration_context: Context) -> Iterator[Context]:
    """The github and cloudflare facades on; broker caches off so each call re-reads."""
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


async def _seed_api(ctx: Context, vendor: str, name: str, spec: str) -> None:
    """Import and promote ``spec`` (the registry records the vendor's slug, ``github-com``)."""
    result = await ImportHandler(ctx).execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={
            "sources": [
                {
                    "type": "inline",
                    "content": spec,
                    "filename": f"{name}.json",
                    "vendor": vendor,
                    "api_name": name,
                    "version": _VERSION,
                }
            ]
        },
        created_by=_SEED_MARKER,
    )
    (revision,) = result.body["revisions"]
    api = revision["api"]
    await RevisionService(ctx).promote(
        api["vendor"],
        api["name"],
        api["version"],
        revision["revision_id"],
        identity=Identity(sub=_SEED_MARKER, permissions=["registry:approve-host-change"]),
    )


async def _seed_credential(ctx: Context, name: str, secret: str) -> str:
    credential = Credential(
        type=StoredCredentialType.STATIC_BEARER_TOKEN,
        name=name,
        api_vendor=slugify_api_field(_GITHUB_VENDOR),
        api_name=_GITHUB_NAME,
        api_version=_VERSION,
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
            email=f"{_OWNER_ID}@proxy.test",
            first_name="Proxy",
            last_name="Owner",
            created_by=_SEED_MARKER,
        )
        agent = await AgentRepository.create(
            session,
            name="cli-proxy-agent",
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
    """Bind the credential with one rule allowing GET and POST on every path."""
    async with ctx.admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=agent_id, credential_id=credential_id
            )
        )
        await session.commit()
    async with ctx.control_db.session() as session:
        session.add(
            AgentPermissionRule(
                agent_id=agent_id,
                credential_id=credential_id,
                effect="allow",
                methods=["GET", "POST"],
                path=".*",
                sequence=1,
            )
        )
        await session.commit()


@pytest.fixture()
async def env(proxy_ctx: Context) -> AsyncGenerator[_Env, None]:
    ctx = proxy_ctx
    await _truncate(ctx)
    await _seed_api(ctx, _GITHUB_VENDOR, _GITHUB_NAME, _GITHUB_SPEC)
    await _seed_api(ctx, _CLOUDFLARE_VENDOR, _CLOUDFLARE_NAME, _CLOUDFLARE_SPEC)
    main_id = await _seed_credential(ctx, "gh-main", _MAIN_SECRET)
    other_id = await _seed_credential(ctx, "gh-other", _OTHER_SECRET)
    agent_id = await _seed_agent(ctx)
    await _bind(ctx, agent_id, main_id)

    upstream = _Upstream()
    broker = create_broker_app(ctx)
    install_broker_registry_resolver(broker, ctx)
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle))
    runner = HttpRunner(mock_client)
    broker.dependency_overrides[get_cli_proxy_runner] = lambda: runner
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
    env: _Env, *, vendor: str = "github", credential_name: str | None = None
) -> tuple[str, str, str]:
    """Returns ``(access_token, invocation_token, trace_id)``."""
    access, _refresh = await TokenService(env.ctx).issue_pair(
        env.agent_id, ActorType.AGENT, [BROKER_EXECUTE_PERMISSION, EXECUTIONS_READ]
    )
    minted = await CliInvocationService(env.ctx).mint(
        CliInvocationMintPayload(
            vendor=vendor, credential_name=credential_name, command_title="api user"
        ),
        identity=Identity(sub=env.agent_id, actor_type=ActorType.AGENT),
        parent_token=access,
    )
    return access, minted.token, minted.invocation.trace_id


async def _call(env: _Env, method: str, url: str, token: str, **kwargs: Any) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    async with AsyncClient(
        transport=ASGITransport(app=env.broker), base_url="http://broker.test"
    ) as client:
        return await client.request(method, f"/_facade/proxy/{url}", headers=headers, **kwargs)


def _problem(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(_PROBLEM_JSON)
    body: dict[str, Any] = response.json()
    assert body["status"] == status
    return body


def _github_error(response: httpx.Response, status: int) -> str:
    """Assert a GitHub REST error body; returns its message."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body: dict[str, Any] = response.json()
    assert body["status"] == str(status)
    message: str = body["message"]
    assert message.startswith("Jentic: ")
    return message


def _cloudflare_error(response: httpx.Response, status: int) -> str:
    """Assert a Cloudflare API v4 error envelope; returns its message."""
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["success"] is False
    assert body["result"] is None
    (error,) = body["errors"]
    assert error["code"] == status
    message: str = error["message"]
    return message


def _assert_no_jentic_token_upstream(request: httpx.Request, *tokens: str) -> None:
    for name, value in request.headers.items():
        assert "cit_" not in value, name
        for token in tokens:
            assert token not in value, name


async def _records(ctx: Context, trace_id: str) -> list[ExecutionRecord]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(ExecutionRecord).where(ExecutionRecord.trace_id == trace_id)
        )
        return list(rows.scalars().all())


# ── success path ─────────────────────────────────────────────────────────────


async def test_call_reaches_the_vendor_with_the_stored_credential(env: _Env) -> None:
    access, token, trace_id = await _mint(env)

    fetched = await _call(
        env,
        "GET",
        "https://api.github.com/repos/o/r/contents/dir%2Ffile.md?ref=main&x=a%20b",
        token,
        headers={"X-Debug": f"token={token}"},
    )
    created = await _call(
        env, "POST", "https://api.github.com/repos/o/r/issues", token, json={"title": "t"}
    )

    assert fetched.status_code == 200, fetched.text
    assert created.status_code == 200, created.text
    get_request, post_request = env.upstream.requests
    assert get_request.url.host == "api.github.com"
    assert get_request.url.raw_path == b"/repos/o/r/contents/dir%2Ffile.md?ref=main&x=a%20b"
    assert json.loads(post_request.content) == {"title": "t"}
    for request in (get_request, post_request):
        assert request.headers["authorization"] == f"Bearer {_MAIN_SECRET}"
        _assert_no_jentic_token_upstream(request, token, access)
        assert "x-debug" not in request.headers
    assert fetched.headers["jentic-credential-id"] == env.main_credential_id
    assert "set-cookie" not in fetched.headers
    assert fetched.headers["x-ratelimit-remaining"] == "4999"
    assert fetched.headers["link"] == '<https://api.github.com/user/repos?page=2>; rel="next"'
    assert fetched.headers["x-github-request-id"] == "ABCD:1234"

    records = await _records(env.ctx, trace_id)
    assert sorted((r.operation_method, r.http_status) for r in records) == [
        ("GET", 200),
        ("POST", 200),
    ]
    assert {r.actor_id for r in records} == {env.agent_id}


async def test_calls_group_under_the_token_trace_even_with_a_client_traceparent(
    env: _Env,
) -> None:
    access, token, trace_id = await _mint(env)
    client_traceparent = f"00-{_CLIENT_TRACE}-{'1' * 16}-01"

    for _ in range(2):
        response = await _call(
            env,
            "GET",
            "https://api.github.com/user",
            token,
            headers={"traceparent": client_traceparent},
        )
        assert response.status_code == 200, response.text

    assert len(await _records(env.ctx, trace_id)) == 2
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


async def test_credential_name_chooses_between_two_credentials(env: _Env) -> None:
    await _bind(env.ctx, env.agent_id, env.other_credential_id)
    _access, unnamed, _trace = await _mint(env)
    _access, named_other, _trace = await _mint(env, credential_name="gh-other")

    ambiguous = await _call(env, "GET", "https://api.github.com/user", unnamed)
    other = await _call(
        env,
        "GET",
        "https://api.github.com/user",
        named_other,
        headers={"Jentic-Credential-Name": "gh-main"},
    )

    _github_error(ambiguous, 409)
    assert other.status_code == 200, other.text
    (request,) = env.upstream.requests
    assert request.headers["authorization"] == f"Bearer {_OTHER_SECRET}"


# ── refusals, in the error shape of the URL's vendor ─────────────────────────


async def test_access_token_is_refused(env: _Env) -> None:
    access, _token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "https://api.github.com/user", access)

    _github_error(response, 401)
    assert env.upstream.requests == []


async def test_url_of_another_vendors_api_is_refused(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env, vendor="github")

    response = await _call(env, "GET", "https://api.cloudflare.com/client/v4/zones", token)

    message = _cloudflare_error(response, 403)
    assert message.startswith("Jentic: ")
    assert env.upstream.requests == []


async def test_unregistered_github_url_is_githubs_404(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "https://api.github.com/not/registered", token)

    _github_error(response, 404)
    assert env.upstream.requests == []


async def test_unregistered_url_of_no_shaped_vendor_is_a_problem_404(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "https://unregistered.jentic.test/anything", token)

    body = _problem(response, 404)
    assert body["type"] == "operation_not_found"
    assert env.upstream.requests == []


async def test_switched_off_vendor_is_a_404(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)
    env.ctx.config.cli_facades.vendors.github.enabled = False

    response = await _call(env, "GET", "https://api.github.com/user", token)

    message = _github_error(response, 404)
    assert "not enabled" in message
    assert env.upstream.requests == []


async def test_proxy_route_never_reaches_the_catch_all_without_a_url(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "", token)

    _problem(response, 404)
    assert env.upstream.requests == []
