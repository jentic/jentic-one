"""End to end: the GitHub CLI facade (host mode), through the real broker app and pipeline.

Seeds minimal GitHub REST, GraphQL and uploads OpenAPI specs (each its own
API under the ``github`` vendor, imported and promoted in the registry), an
agent bound to one vendor-wide bearer credential with permission rules, and
mints ``cit_`` invocation tokens through the real ``CliInvocationService``.
Requests are sent to the real standalone broker app with
``Host: github.jentic.test``, the configured facade host, as ``gh`` sends
them with ``GH_HOST`` set; the upstream is the broker's own ``HttpRunner``
over an ``httpx`` ``MockTransport`` that records what GitHub would receive.

Covers: REST, GraphQL and upload calls forwarded to github.com with the
stored credential and never the ``cit_``/``at_`` token; pagination ``Link``
and ``Location`` pointed back at the facade; ``GET /api/v3/meta`` answered
locally; calls recorded under the token's trace id; and every refusal
(unregistered path, a path outside the GitHub layout, rule deny, hold, wrong
token kind or vendor) answered in GitHub's error shape with the pipeline's
status. On any other host, or with the facade switched off, the facade
routes do not engage. GraphQL calls are judged on their top-level fields:
the GitHub GraphQL presets, aliases and fragments, hard-denied and held
mutations, and requests that cannot be inspected.
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
from jentic_one.auth.services.cli_invocation_service import CliInvocationService
from jentic_one.auth.services.schemas.cli_invocations import CliInvocationMintPayload
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.setup import build_queued_execution_authorizer
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.deps import get_http_runner
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
from jentic_one.shared.jobs.protocols import QueuedExecutionRequest
from jentic_one.shared.models import ActorStatus, ActorType, StoredCredentialType
from jentic_one.shared.models.events import EventType
from jentic_one.shared.permissions.github_graphql import GITHUB_GRAPHQL_PRESETS
from jentic_one.wiring import install_broker_registry_resolver

pytestmark = pytest.mark.integration

_SEED_MARKER = "usr_gh_facade_seed"
_OWNER_ID = "usr_gh_facade_owner"
_VENDOR = "github"
_VERSION = "1.0.0"
_HOST = "github.jentic.test"
_FACADE = f"https://{_HOST}"
_SECRET = "ghp-secret-main"  # pragma: allowlist secret
_CLIENT_TRACE = "c" * 32

_PATH_PARAMS: dict[str, dict[str, Any]] = {
    name: {"name": name, "in": "path", "required": True, "schema": {}}
    for name in ("owner", "repo", "release_id")
}
_OK = {"200": {"description": "OK"}}
_BEARER = {
    "components": {"securitySchemes": {"token": {"type": "http", "scheme": "bearer"}}},
    "security": [{"token": []}],
}


def _spec(title: str, server: str, paths: dict[str, Any]) -> str:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": title, "version": _VERSION},
            "servers": [{"url": server}],
            **_BEARER,
            "paths": paths,
        }
    )


_REPO = [_PATH_PARAMS["owner"], _PATH_PARAMS["repo"]]

#: ``(api_name, spec)`` for each API the facade's calls resolve to. GraphQL and
#: uploads are APIs of their own next to REST, all under the one vendor.
_SPECS: tuple[tuple[str, str], ...] = (
    (
        "rest",
        _spec(
            "GitHub REST API",
            "https://api.github.com",
            {
                "/repos/{owner}/{repo}/pulls": {
                    "get": {"operationId": "pullsList", "parameters": _REPO, "responses": _OK}
                },
                "/repos/{owner}/{repo}/issues": {
                    "post": {"operationId": "issuesCreate", "parameters": _REPO, "responses": _OK}
                },
                "/repos/{owner}/{repo}": {
                    "delete": {"operationId": "reposDelete", "parameters": _REPO, "responses": _OK}
                },
            },
        ),
    ),
    (
        "graphql",
        _spec(
            "GitHub GraphQL API",
            "https://api.github.com",
            {"/graphql": {"post": {"operationId": "graphql", "responses": _OK}}},
        ),
    ),
    (
        "uploads",
        _spec(
            "GitHub release asset uploads",
            "https://uploads.github.com",
            {
                "/repos/{owner}/{repo}/releases/{release_id}/assets": {
                    "post": {
                        "operationId": "reposUploadReleaseAsset",
                        "parameters": [*_REPO, _PATH_PARAMS["release_id"]],
                        "responses": {"201": {"description": "Created"}},
                    }
                }
            },
        ),
    ),
)


def _json_response(status: int, payload: Any, headers: dict[str, str]) -> httpx.Response:
    """A response with an async body, as a real upstream's: the broker streams it through."""
    body = json.dumps(payload).encode()

    async def _content() -> AsyncIterator[bytes]:
        yield body

    return httpx.Response(status, content=_content(), headers=headers)


@dataclass
class _Upstream:
    """Records every request GitHub would receive and answers like github.com."""

    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        common = {
            "Content-Type": "application/json; charset=utf-8",
            "Set-Cookie": "_gh_sess=upstream-session; Domain=.github.com",
            "X-OAuth-Scopes": "repo, admin:org",
            "X-Accepted-OAuth-Scopes": "repo",
            "X-GitHub-Request-Id": "ABCD:1234",
            "X-RateLimit-Remaining": "4999",
            "ETag": 'W/"abc"',
        }
        if request.url.host == "uploads.github.com":
            return _json_response(
                201,
                {"id": 7, "name": "a.zip"},
                {
                    **common,
                    "Location": "https://api.github.com/repos/o/r/releases/assets/7",
                },
            )
        if request.url.path == "/graphql":
            return _json_response(200, {"data": {"viewer": {"login": "octocat"}}}, common)
        link = (
            '<https://api.github.com/repositories/42/pulls?page=2>; rel="next", '
            '<https://api.github.com/repositories/42/pulls?page=3>; rel="last", '
            '<https://docs.github.com/rest>; rel="help"'
        )
        return _json_response(200, [{"number": 1}], {**common, "Link": link})


@dataclass
class _Env:
    ctx: Context
    broker: FastAPI
    upstream: _Upstream
    agent_id: str
    credential_id: str


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def facade_ctx(integration_context: Context) -> Iterator[Context]:
    """GitHub (and cloudflare) facades on; broker caches off so each call re-reads."""
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
                "github": {"enabled": True, "host": _HOST},
                "cloudflare": {"enabled": True},
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


async def _seed_apis(ctx: Context) -> None:
    for api_name, spec in _SPECS:
        result = await ImportHandler(ctx).execute(
            job_id=str(uuid.uuid4()),
            db=None,
            payload={
                "sources": [
                    {
                        "type": "inline",
                        "content": spec,
                        "filename": f"github-{api_name}.json",
                        "vendor": _VENDOR,
                        "api_name": api_name,
                        "version": _VERSION,
                    }
                ]
            },
            created_by=_SEED_MARKER,
        )
        revision_id = result.body["revisions"][0]["revision_id"]
        await RevisionService(ctx).promote(
            _VENDOR,
            api_name,
            _VERSION,
            revision_id,
            identity=Identity(sub=_SEED_MARKER, permissions=["registry:approve-host-change"]),
        )


async def _seed_credential(ctx: Context) -> str:
    """One vendor-wide credential: it covers the REST, GraphQL and uploads APIs."""
    credential = Credential(
        type=StoredCredentialType.STATIC_BEARER_TOKEN, name="gh-main", api_vendor=_VENDOR
    )
    async with ctx.control_db.session() as session:
        session.add(credential)
        await session.flush()
        session.add(
            TokenValueCredential(
                credential_id=credential.id,
                encrypted_token_value=ctx.encryption.encrypt(_SECRET),
            )
        )
        await session.commit()
        return credential.id


async def _seed_agent(ctx: Context, credential_id: str) -> str:
    """An agent bound to the credential: repo DELETE denied, issue POST held, else allowed."""
    async with ctx.admin_db.transaction() as session:
        await UserRepository.create(
            session,
            id=_OWNER_ID,
            email=f"{_OWNER_ID}@gh.test",
            first_name="Gh",
            last_name="Owner",
            created_by=_SEED_MARKER,
        )
        agent = await AgentRepository.create(
            session,
            name="gh-facade-agent",
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
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=agent.id, credential_id=credential_id
            )
        )
        agent_id = agent.id
    async with ctx.control_db.session() as session:
        for sequence, (effect, methods, path) in enumerate(
            [
                ("deny", ["DELETE"], "/repos/.*"),
                ("require-approval", ["POST"], "/repos/[^/]+/[^/]+/issues"),
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
    return agent_id


@pytest.fixture()
async def env(facade_ctx: Context) -> AsyncGenerator[_Env, None]:
    ctx = facade_ctx
    await _truncate(ctx)
    await _seed_apis(ctx)
    credential_id = await _seed_credential(ctx)
    agent_id = await _seed_agent(ctx, credential_id)

    upstream = _Upstream()
    broker = create_broker_app(ctx)
    install_broker_registry_resolver(broker, ctx)
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle))
    runner = HttpRunner(mock_client)
    broker.dependency_overrides[facades._GitHubRunner.dependency] = lambda: runner
    yield _Env(
        ctx=ctx, broker=broker, upstream=upstream, agent_id=agent_id, credential_id=credential_id
    )
    await mock_client.aclose()
    await _truncate(ctx)


async def _mint(env: _Env, *, vendor: str = "github") -> tuple[str, str, str]:
    """Returns ``(access_token, invocation_token, trace_id)``."""
    access, _refresh = await TokenService(env.ctx).issue_pair(
        env.agent_id, ActorType.AGENT, [BROKER_EXECUTE_PERMISSION, EXECUTIONS_READ]
    )
    minted = await CliInvocationService(env.ctx).mint(
        CliInvocationMintPayload(vendor=vendor, command_title="pr list"),
        identity=Identity(sub=env.agent_id, actor_type=ActorType.AGENT),
        parent_token=access,
    )
    return access, minted.token, minted.invocation.trace_id


async def _call(
    env: _Env, method: str, path: str, token: str | None, *, host: str = _HOST, **kwargs: Any
) -> httpx.Response:
    """Send a request as ``gh`` does: ``Authorization: token <t>`` to ``host``."""
    headers = dict(kwargs.pop("headers", {}))
    if token is not None:
        headers["Authorization"] = f"token {token}"
    async with AsyncClient(
        transport=ASGITransport(app=env.broker), base_url=f"http://{host}"
    ) as client:
        return await client.request(method, path, headers=headers, **kwargs)


def _github_error(response: httpx.Response, status: int) -> dict[str, Any]:
    """Assert ``response`` is GitHub's error shape with ``status``; return the body."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/json"
    body: dict[str, Any] = response.json()
    assert set(body) <= {"message", "documentation_url", "status"}
    assert body["status"] == str(status)
    assert str(body["message"]).startswith("Jentic: ")
    return body


def _assert_only_the_stored_credential_upstream(request: httpx.Request, *tokens: str) -> None:
    assert request.headers["authorization"] == f"Bearer {_SECRET}"
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


# ── success paths ────────────────────────────────────────────────────────────


async def test_rest_get_reaches_github_with_link_pointed_at_the_facade(env: _Env) -> None:
    access, token, trace_id = await _mint(env)

    response = await _call(
        env,
        "GET",
        "/api/v3/repos/o/r/pulls?per_page=30&state=open",
        token,
        headers={
            "Accept": "application/vnd.github.merge-info-preview+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "X-Debug": f"token={token}",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == [{"number": 1}]
    # GitHub names the repository by id in pagination links; the facade puts
    # them back in the call's /repos/<owner>/<repo> form, on the facade host.
    assert response.headers["link"] == (
        f'<{_FACADE}/api/v3/repos/o/r/pulls?page=2>; rel="next", '
        f'<{_FACADE}/api/v3/repos/o/r/pulls?page=3>; rel="last", '
        '<https://docs.github.com/rest>; rel="help"'
    )
    assert response.headers["x-github-request-id"] == "ABCD:1234"
    assert response.headers["x-accepted-oauth-scopes"] == "repo"
    assert response.headers["x-ratelimit-remaining"] == "4999"
    assert response.headers["etag"] == 'W/"abc"'
    assert "x-oauth-scopes" not in response.headers
    assert "set-cookie" not in response.headers
    (request,) = env.upstream.requests
    assert str(request.url) == "https://api.github.com/repos/o/r/pulls?per_page=30&state=open"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert request.headers["accept"] == "application/vnd.github.merge-info-preview+json"
    assert "x-debug" not in request.headers
    _assert_only_the_stored_credential_upstream(request, token, access)
    (record,) = await _records(env.ctx, trace_id, count=1)
    assert (record.operation_method, record.operation_path) == (
        "GET",
        "/repos/{owner}/{repo}/pulls",
    )
    assert record.api_name == "rest"


async def test_following_the_next_link_reaches_the_next_page(env: _Env) -> None:
    _access, token, trace_id = await _mint(env)
    first = await _call(env, "GET", "/api/v3/repos/o/r/pulls", token)
    next_url = first.headers["link"].split(">", 1)[0].removeprefix("<")
    assert next_url.startswith(f"{_FACADE}/")

    second = await _call(env, "GET", next_url.removeprefix(_FACADE), token)

    assert second.status_code == 200, second.text
    assert str(env.upstream.requests[-1].url) == "https://api.github.com/repos/o/r/pulls?page=2"
    assert len(await _records(env.ctx, trace_id, count=2)) == 2


async def test_graphql_post_reaches_the_graphql_endpoint(env: _Env) -> None:
    access, token, trace_id = await _mint(env)
    query = {"query": "query { viewer { login } }", "variables": {}}

    response = await _call(env, "POST", "/api/graphql", token, json=query)

    assert response.status_code == 200, response.text
    assert response.json() == {"data": {"viewer": {"login": "octocat"}}}
    (request,) = env.upstream.requests
    assert str(request.url) == "https://api.github.com/graphql"
    assert json.loads(request.content) == query
    _assert_only_the_stored_credential_upstream(request, token, access)
    (record,) = await _records(env.ctx, trace_id, count=1)
    assert (record.operation_method, record.operation_path, record.api_name) == (
        "POST",
        "/graphql",
        "graphql",
    )


async def test_upload_post_reaches_uploads_github_com(env: _Env) -> None:
    access, token, trace_id = await _mint(env)

    response = await _call(
        env,
        "POST",
        "/api/uploads/repos/o/r/releases/1/assets?name=a.zip",
        token,
        content=b"PK\x03\x04 zip bytes",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 201, response.text
    assert response.headers["location"] == f"{_FACADE}/api/v3/repos/o/r/releases/assets/7"
    (request,) = env.upstream.requests
    assert str(request.url) == "https://uploads.github.com/repos/o/r/releases/1/assets?name=a.zip"
    assert request.content == b"PK\x03\x04 zip bytes"
    assert request.headers["content-type"] == "application/octet-stream"
    _assert_only_the_stored_credential_upstream(request, token, access)
    (record,) = await _records(env.ctx, trace_id, count=1)
    assert record.api_name == "uploads"


async def test_meta_is_answered_by_the_facade(env: _Env) -> None:
    _access, token, trace_id = await _mint(env)

    response = await _call(env, "GET", "/api/v3/meta", token)
    unauthenticated = await _call(env, "GET", "/api/v3/meta", None)

    assert response.status_code == 200, response.text
    assert response.json()["installed_version"]
    _github_error(unauthenticated, 401)
    assert env.upstream.requests == []
    assert await _records(env.ctx, trace_id) == []


async def test_calls_group_under_the_token_trace(env: _Env) -> None:
    _access, token, trace_id = await _mint(env)
    client_traceparent = f"00-{_CLIENT_TRACE}-{'1' * 16}-01"

    rest = await _call(
        env, "GET", "/api/v3/repos/o/r/pulls", token, headers={"traceparent": client_traceparent}
    )
    graphql = await _call(env, "POST", "/api/graphql", token, json={"query": "{viewer{login}}"})

    assert rest.status_code == 200, rest.text
    assert graphql.status_code == 200, graphql.text
    assert {record.api_name for record in await _records(env.ctx, trace_id, count=2)} == {
        "rest",
        "graphql",
    }
    assert await _records(env.ctx, _CLIENT_TRACE) == []
    for request in env.upstream.requests:
        assert _CLIENT_TRACE not in request.headers.get("traceparent", "")


# ── refusals, in GitHub's error shape ────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/user/keys",
        "/api/v3/repos/o/r/../../user/keys",
        "/api/v3/repos/o/r/%2e%2e/x",
        "/api/v%33/repos/o/r/pulls",
    ],
)
async def test_unregistered_api_path_is_a_github_404(env: _Env, path: str) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", path, token)

    assert "not found" in _github_error(response, 404)["message"].lower()
    assert env.upstream.requests == []


@pytest.mark.parametrize("path", ["/o/r/pull/1", "/o/r.git/info/refs", "/api", "/"])
async def test_paths_outside_the_github_layout_never_reach_the_catch_all(
    env: _Env, path: str
) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "GET", path, token)
    unauthenticated = await _call(env, "GET", path, None)

    assert "not found" in _github_error(response, 404)["message"].lower()
    _github_error(unauthenticated, 401)
    assert env.upstream.requests == []


async def test_rule_deny_is_a_github_403_with_the_directive(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "DELETE", "/api/v3/repos/o/r", token)

    message = _github_error(response, 403)["message"]
    assert "denied" in message.lower()
    assert len(message) > len("Jentic: Operation denied.")
    assert env.upstream.requests == []


async def test_held_call_is_a_github_409_naming_the_hold(env: _Env) -> None:
    _access, token, trace_id = await _mint(env)

    response = await _call(env, "POST", "/api/v3/repos/o/r/issues", token, json={"title": "x"})

    body = _github_error(response, 409)
    async with env.ctx.admin_db.session() as session:
        (held,) = (await session.execute(select(HeldExecution))).scalars().all()
    assert held.trace_id == trace_id
    assert held.id in body["message"]
    assert f"/executions/holds/{held.id}/result" in body["message"]
    assert "re-run" in body["message"]
    assert held.id in body["documentation_url"]
    assert env.upstream.requests == []


async def test_access_token_is_refused_with_a_github_401(env: _Env) -> None:
    access, _token, _trace_id = await _mint(env)

    response = await _call(env, "GET", "/api/v3/repos/o/r/pulls", access)

    _github_error(response, 401)
    assert env.upstream.requests == []


async def test_token_for_another_vendor_is_refused_with_a_github_401(env: _Env) -> None:
    _access, cloudflare_token, _trace_id = await _mint(env, vendor="cloudflare")

    response = await _call(env, "GET", "/api/v3/repos/o/r/pulls", cloudflare_token)

    _github_error(response, 401)
    assert env.upstream.requests == []


# ── the facade engages on its own host only ──────────────────────────────────


async def test_another_host_does_not_engage_the_facade(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)

    on_broker = await _call(env, "GET", "/api/v3/repos/o/r/pulls", token, host="broker.test")
    spoofed = await _call(
        env,
        "GET",
        "/api/v3/repos/o/r/pulls",
        token,
        host="broker.test",
        headers={"X-Forwarded-Host": _HOST},
    )

    for response in (on_broker, spoofed):
        # The forward proxy answered: Problem Details, not GitHub's shape.
        assert response.headers["content-type"] == "application/problem+json", response.text
    assert env.upstream.requests == []


async def test_switched_off_facade_does_not_engage(env: _Env) -> None:
    _access, token, _trace_id = await _mint(env)
    env.ctx.config.cli_facades.vendors.github.enabled = False

    response = await _call(env, "GET", "/api/v3/repos/o/r/pulls", token)

    assert response.headers["content-type"] == "application/problem+json", response.text
    assert env.upstream.requests == []


# ── GraphQL top-level-field rules ────────────────────────────────────────────


async def _use_preset(env: _Env, preset: str) -> None:
    """Replace the agent's rules with a GitHub GraphQL preset."""
    async with env.ctx.control_db.session() as session:
        await session.execute(delete(AgentPermissionRule))
        for sequence, rule in enumerate(GITHUB_GRAPHQL_PRESETS[preset], start=1):
            session.add(
                AgentPermissionRule(
                    agent_id=env.agent_id,
                    credential_id=env.credential_id,
                    effect=rule["effect"],
                    path=rule["path"],
                    match_mode=rule["match_mode"],
                    comment=rule.get("_comment"),
                    sequence=sequence,
                )
            )
        await session.commit()


async def _held(env: _Env) -> list[HeldExecution]:
    async with env.ctx.admin_db.session() as session:
        return list((await session.execute(select(HeldExecution))).scalars().all())


_SNEAKY_FRAGMENT = (
    "mutation { ...Wrapped } "
    "fragment Wrapped on Mutation { ... on Mutation { createPullRequest: deleteRef("
    'input: {refId: "x"}) { clientMutationId } } }'
)


async def test_read_only_preset_allows_a_query(env: _Env) -> None:
    await _use_preset(env, "read_only")
    _access, token, trace_id = await _mint(env)

    response = await _call(
        env,
        "POST",
        "/api/graphql",
        token,
        json={"query": "query Q { viewer { login } __typename }", "operationName": "Q"},
    )

    assert response.status_code == 200, response.text
    assert len(env.upstream.requests) == 1
    assert len(await _records(env.ctx, trace_id, count=1)) == 1


async def test_read_only_preset_denies_a_mutation_naming_the_field(env: _Env) -> None:
    await _use_preset(env, "read_only")
    _access, token, _trace_id = await _mint(env)

    response = await _call(
        env,
        "POST",
        "/api/graphql",
        token,
        json={"query": "mutation { addComment(input: {}) { clientMutationId } }"},
    )

    message = _github_error(response, 403)["message"]
    assert "mutation field 'addComment'" in message
    assert "operator" in message
    assert env.upstream.requests == []


async def test_pr_write_with_approval_holds_create_pull_request(env: _Env) -> None:
    await _use_preset(env, "pr_write_with_approval")
    _access, token, trace_id = await _mint(env)

    response = await _call(
        env,
        "POST",
        "/api/graphql",
        token,
        json={"query": "mutation { createPullRequest(input: {}) { pullRequest { url } } }"},
    )

    body = _github_error(response, 409)
    (held,) = await _held(env)
    assert held.trace_id == trace_id
    assert held.id in body["message"]
    assert env.upstream.requests == []


async def test_approved_graphql_hold_passes_the_run_time_recheck_on_its_body(env: _Env) -> None:
    """The worker re-inspects the held body: the approved mutation runs, another is refused."""
    await _use_preset(env, "pr_write_with_approval")
    _access, token, _trace_id = await _mint(env)
    held_body = b'{"query": "mutation { createPullRequest(input: {}) { a } }"}'
    response = await _call(
        env,
        "POST",
        "/api/graphql",
        token,
        content=held_body,
        headers={"Content-Type": "application/json"},
    )
    _github_error(response, 409)
    (held,) = await _held(env)
    async with env.ctx.admin_db.transaction() as session:
        await session.execute(
            update(HeldExecution).where(HeldExecution.id == held.id).values(state="approved")
        )
    authorizer = build_queued_execution_authorizer(env.ctx)

    def _queued(body: bytes) -> QueuedExecutionRequest:
        return QueuedExecutionRequest(
            actor_id=env.agent_id,
            actor_type="agent",
            method="POST",
            upstream_url="https://api.github.com/graphql",
            api_vendor=_VENDOR,
            api_name="graphql",
            api_version=_VERSION,
            operation_id="graphql",
            relative_path="/graphql",
            credential_id=env.credential_id,
            job_id=held.job_id,
            body=body,
        )

    approved = await authorizer.authorize(_queued(held_body))
    swapped = await authorizer.authorize(
        _queued(b'{"query": "mutation { deleteRef(input: {}) { a } }"}')
    )

    assert approved.allowed is True, approved.problem
    assert swapped.allowed is False
    assert swapped.problem is not None
    assert "'deleteRef'" in str(swapped.problem["detail"])


@pytest.mark.parametrize(
    "query",
    [
        'mutation { createPullRequest: deleteRef(input: {refId: "x"}) { clientMutationId } }',
        _SNEAKY_FRAGMENT,
        "mutation { createPullRequest(input: {}) { a } ...D } "
        "fragment D on Mutation { deleteRef(input: {}) { a } }",
    ],
)
async def test_aliased_or_fragment_wrapped_mutation_is_still_caught(env: _Env, query: str) -> None:
    await _use_preset(env, "pr_write_with_approval")
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, "POST", "/api/graphql", token, json={"query": query})

    assert "'deleteRef'" in _github_error(response, 403)["message"]
    assert await _held(env) == []
    assert env.upstream.requests == []


@pytest.mark.parametrize(
    "field", ["createBranchProtectionRule", "updateRepositoryRuleset", "updateTeamsRepository"]
)
async def test_hard_denied_mutation_is_denied_despite_an_allow_all_rule(
    env: _Env, field: str
) -> None:
    # The seeded rules allow every GET and POST.
    _access, token, _trace_id = await _mint(env)

    response = await _call(
        env,
        "POST",
        "/api/graphql",
        token,
        json={"query": f"mutation {{ ok: addComment {{ a }} {field}(input: {{}}) {{ a }} }}"},
    )

    message = _github_error(response, 403)["message"]
    assert f"'{field}'" in message
    assert "never allowed" in message
    assert env.upstream.requests == []
    async with env.ctx.admin_db.session() as session:
        (event,) = (
            (await session.execute(select(Event).where(Event.type == EventType.PBAC_DENIED)))
            .scalars()
            .all()
        )
    assert event.data["reason"] == "graphql_field_hard_denied"
    assert event.data["graphql_field"] == field


async def test_ref_mutation_allowed_by_a_rule_is_held(env: _Env) -> None:
    # The seeded rules allow every GET and POST.
    _access, token, _trace_id = await _mint(env)

    response = await _call(
        env,
        "POST",
        "/api/graphql",
        token,
        json={"query": "mutation { createCommitOnBranch(input: {}) { commit { oid } } }"},
    )

    _github_error(response, 409)
    assert len(await _held(env)) == 1
    assert env.upstream.requests == []


@pytest.mark.parametrize(
    ("method", "kwargs", "expected"),
    [
        (
            "POST",
            {"json": [{"query": "{ viewer { login } }"}, {"query": "{ viewer { id } }"}]},
            "batch",
        ),
        (
            "POST",
            {"json": {"extensions": {"persistedQuery": {"version": 1, "sha256Hash": "a"}}}},
            "persisted",
        ),
        (
            "POST",
            {"json": {"query": "query A { viewer { login } } mutation B { addComment { a } }"}},
            "2 operations",
        ),
        (
            "POST",
            {
                "content": b'{"query": "{ viewer { login } }", "query": "mutation { a }"}',
                "headers": {"Content-Type": "application/json"},
            },
            "duplicate key",
        ),
        ("POST", {"json": {"query": "subscription { e { id } }"}}, "subscription"),
        ("POST", {"json": {"query": "mutation {"}}, "not a valid GraphQL document"),
        ("GET", {"params": {"query": "{ viewer { login } }"}}, "GraphQL over GET"),
    ],
)
async def test_uninspectable_graphql_request_is_rejected(
    env: _Env, method: str, kwargs: dict[str, Any], expected: str
) -> None:
    _access, token, _trace_id = await _mint(env)

    response = await _call(env, method, "/api/graphql", token, **kwargs)

    message = _github_error(response, 400)["message"]
    assert expected in message
    assert "one GraphQL query or mutation per request" in message
    assert env.upstream.requests == []


async def test_forward_proxy_applies_the_same_graphql_rules(env: _Env) -> None:
    """The GitHub GraphQL endpoint is inspected on the forward proxy too, not only the facade."""
    access, _token, _trace_id = await _mint(env)
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(env.upstream.handle))
    env.broker.dependency_overrides[get_http_runner] = lambda: HttpRunner(upstream_client)

    async with AsyncClient(
        transport=ASGITransport(app=env.broker), base_url="http://broker.test"
    ) as client:
        response = await client.post(
            "/api.github.com/graphql",
            headers={"Authorization": f"Bearer {access}"},
            json={"query": "mutation { deleteBranchProtectionRule(input: {}) { a } }"},
        )
        batch = await client.post(
            "/api.github.com/graphql",
            headers={"Authorization": f"Bearer {access}"},
            json=[{"query": "{ viewer { login } }"}],
        )

    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "graphql_field_denied"
    assert batch.status_code == 400, batch.text
    assert batch.json()["type"] == "graphql_request_rejected"
    assert env.upstream.requests == []
    await upstream_client.aclose()
