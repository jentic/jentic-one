"""Integration tests: a standalone admin process runs the full platform token chain (#1445).

In a split deployment the admin surface runs without the auth surface in its
process. An OAuth/MCP agent still polls the broker's async ``_links.self``
(``GET /jobs/{id}``) on admin with its opaque ``at_`` access token, so the
standalone admin app must resolve ``at_`` against the admin DB's
``access_tokens`` table exactly as the combined app does — including every
kill switch on that path (revocation, expiry, a disabled agent, a deactivated
OAuth client, a revoked consent grant) and the permission and query-scoping gates behind it.

Each fail-closed ``at_`` case first proves the token is accepted (200) on the
same app and then flips one gate, so it can only pass when the ``at_`` branch
runs: a chain without it 401s every ``at_`` token up front. The token types the
admin surface already accepted (HS256 login JWTs, ``jak_`` keys) and the
retired ``sak_`` refusal are pinned too, so the verifier swap cannot regress
them.

The app is the real standalone admin factory (``admin.web.app.create_app``) on
a context scoped exactly like an ``apps: [admin]`` process
(``allowed_dbs`` from ``__main__._expand_allowed_dbs``), driven in-process over
ASGI so it runs on the test's event loop (SQLite and Postgres alike).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import (
    AgentCredentialRepository,
    AgentRepository,
    JobRepository,
    OAuthClientGrantRepository,
    OAuthClientRepository,
)
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.auth.api_key_resolver import RETIRED_SERVICE_ACCOUNT_KEY_DETAIL
from jentic_one.shared.auth.identity import Identity, LoginPayload
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, JobKind, JobStatus
from jentic_one.shared.web.app_factory import create_combined_app
from tests.integration.auth.seeds import (
    CLIENT_ID,
    SEED_MARKER,
    SEED_PASSWORD,
    mint_grant_channel_tokens,
    seed_agent,
    seed_client,
    seed_password_user,
    seed_permissions,
    seed_user,
)

pytestmark = pytest.mark.integration

_OWNER_ID = "usr_split_admin_owner"
_LOGIN_USER_ID = "usr_split_admin_login"


@pytest.fixture()
async def admin_only_ctx(integration_config: AppConfig) -> AsyncGenerator[Context, None]:
    """A context scoped like a standalone ``apps: [admin]`` process (same DB URLs)."""
    async with Context(
        integration_config, allowed_dbs=_expand_allowed_dbs(["admin"], integration_config)
    ) as ctx:
        yield ctx


@pytest.fixture()
def admin_app(admin_only_ctx: Context) -> FastAPI:
    return create_admin_app(admin_only_ctx)


@pytest.fixture()
async def owner_id(integration_context: Context, clean_grants: None) -> str:
    """The seeded owning user; removed (with its agents) by ``clean_grants``."""
    return await seed_user(integration_context, _OWNER_ID)


@pytest.fixture()
async def created_job_ids(integration_context: Context) -> AsyncGenerator[list[str], None]:
    """Job ids a test creates; deleted afterwards."""
    job_ids: list[str] = []
    yield job_ids
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Job).where(Job.id.in_(job_ids)))
        await session.commit()


async def _create_job(ctx: Context, created_by: str, job_ids: list[str]) -> str:
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.COMPLETED, created_by=created_by
        )
        await session.commit()
        job_ids.append(job.id)
        return job.id


async def _seed_agent_with_job(
    ctx: Context, owner_id: str, job_ids: list[str], *, scopes: list[str]
) -> tuple[str, str]:
    """An active agent holding ``scopes`` and a completed job it created."""
    agent_id = await seed_agent(ctx, owner_id=owner_id, scopes=scopes)
    return agent_id, await _create_job(ctx, agent_id, job_ids)


def _client(app: FastAPI, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _mint_agent_access_token(ctx: Context, agent_id: str) -> str:
    access, _refresh = await TokenService(ctx).issue_pair(agent_id, ActorType.AGENT, ["jobs:read"])
    assert access.startswith("at_")
    return access


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── at_ accepted, then each kill switch on the same app ─────────────────────


async def test_standalone_admin_accepts_agent_access_token(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    token = await _mint_agent_access_token(integration_context, agent_id)

    async with _client(admin_app, token) as client:
        resp = await client.get(f"/jobs/{job_id}")
        unknown = await client.get(
            f"/jobs/{job_id}", headers={"Authorization": "Bearer at_not-a-real-token"}
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == job_id
    assert unknown.status_code == 401, unknown.text


async def _revoke(ctx: Context, token: str, agent_id: str) -> None:
    await TokenService(ctx).revoke(
        token, identity=Identity(sub=agent_id, actor_type=ActorType.AGENT)
    )


async def _expire(ctx: Context, token: str, _agent_id: str) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.token_hash == _token_hash(token))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()


async def _disable_agent(ctx: Context, _token: str, agent_id: str) -> None:
    async with ctx.admin_db.session() as session:
        await AgentRepository.update_status(session, agent_id, ActorStatus.DISABLED)
        await session.commit()


@pytest.mark.parametrize(
    "kill_switch",
    [_revoke, _expire, _disable_agent],
    ids=["revoked", "expired", "agent-disabled"],
)
async def test_standalone_admin_rejects_agent_access_token_after_kill_switch(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
    kill_switch: object,
) -> None:
    """A token the standalone admin accepted stops resolving once a gate flips."""
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    token = await _mint_agent_access_token(integration_context, agent_id)

    async with _client(admin_app, token) as client:
        before = await client.get(f"/jobs/{job_id}")
        await kill_switch(integration_context, token, agent_id)  # type: ignore[operator]
        after = await client.get(f"/jobs/{job_id}")

    assert before.status_code == 200, before.text
    assert after.status_code == 401, after.text


async def test_standalone_admin_rejects_user_access_token_after_client_deactivated(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    """A user ``at_`` issued to an OAuth client dies with the client."""
    await seed_permissions(integration_context, owner_id, ["jobs:read"])
    await seed_client(integration_context, allowed_scopes=["jobs:read"])
    job_id = await _create_job(integration_context, owner_id, created_job_ids)
    token, _refresh = await TokenService(integration_context).issue_pair(
        owner_id, ActorType.USER, ["jobs:read"], oauth_client_id=CLIENT_ID
    )

    async with _client(admin_app, token) as client:
        before = await client.get(f"/jobs/{job_id}")
        async with integration_context.admin_db.session() as session:
            oauth_client = await OAuthClientRepository.get_by_client_id(session, CLIENT_ID)
            assert oauth_client is not None
            await OAuthClientRepository.deactivate(session, oauth_client.id)
            await session.commit()
        after = await client.get(f"/jobs/{job_id}")

    assert before.status_code == 200, before.text
    assert after.status_code == 401, after.text


async def test_standalone_admin_rejects_grant_channel_access_token_after_grant_revoked(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    """An agent ``at_`` minted through a consent grant dies with the grant."""
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    await seed_client(integration_context, allowed_scopes=["jobs:read"])
    grant_id, token, _refresh, _id_token = await mint_grant_channel_tokens(
        integration_context, user_id=owner_id, agent_id=agent_id, grant_scopes=["jobs:read"]
    )

    async with _client(admin_app, token) as client:
        before = await client.get(f"/jobs/{job_id}")
        # Flip only the grant row, so the 401 can come from nothing but the grant gate.
        async with integration_context.admin_db.session() as session:
            assert await OAuthClientGrantRepository.revoke(session, grant_id)
            await session.commit()
        after = await client.get(f"/jobs/{job_id}")

    assert before.status_code == 200, before.text
    assert after.status_code == 401, after.text


# ── at_ authenticates, then the permission and scoping gates decide ─────────


async def test_standalone_admin_access_token_without_permission_is_forbidden(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    """An agent ``at_`` lacking ``jobs:read`` authenticates but is refused (403)."""
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["capabilities:read"]
    )
    token = await _mint_agent_access_token(integration_context, agent_id)

    async with _client(admin_app, token) as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 403, resp.text


async def test_standalone_admin_access_token_cannot_read_another_actors_job(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    """Query scoping still applies: another actor's job is indistinguishable from none."""
    agent_id, own_job = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    _other_agent, other_job = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    token = await _mint_agent_access_token(integration_context, agent_id)

    async with _client(admin_app, token) as client:
        own = await client.get(f"/jobs/{own_job}")
        resp = await client.get(f"/jobs/{other_job}")

    assert own.status_code == 200, own.text
    assert resp.status_code == 404, resp.text


async def test_standalone_admin_agent_access_token_refused_on_operator_route(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    """An agent ``at_`` valid for its own jobs cannot reach an operator route."""
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    token = await _mint_agent_access_token(integration_context, agent_id)

    async with _client(admin_app, token) as client:
        own_job = await client.get(f"/jobs/{job_id}")
        users = await client.get("/users")

    assert own_job.status_code == 200, own_job.text
    assert users.status_code == 403, users.text


# ── Token types the admin surface already accepted or refused ──────────────


async def test_standalone_admin_refuses_retired_service_account_key(admin_app: FastAPI) -> None:
    async with _client(admin_app, f"sak_{secrets.token_hex(16)}") as client:
        resp = await client.get("/jobs")

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == RETIRED_SERVICE_ACCOUNT_KEY_DETAIL


async def test_standalone_admin_accepts_agent_api_key(
    integration_context: Context,
    admin_app: FastAPI,
    owner_id: str,
    created_job_ids: list[str],
) -> None:
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    api_key = f"jak_{secrets.token_hex(16)}"
    async with integration_context.admin_db.session() as session:
        await AgentCredentialRepository.create(
            session, agent_id=agent_id, api_key_hash=_token_hash(api_key), created_by=SEED_MARKER
        )
        await session.commit()

    try:
        async with _client(admin_app, api_key) as client:
            resp = await client.get(f"/jobs/{job_id}")
    finally:
        async with integration_context.admin_db.session() as session:
            await session.execute(
                delete(AgentCredential).where(AgentCredential.agent_id == agent_id)
            )
            await session.commit()

    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == job_id


async def test_standalone_admin_accepts_login_jwt(
    integration_context: Context,
    admin_app: FastAPI,
    clean_grants: None,
    clean_user_secrets: None,
    created_job_ids: list[str],
) -> None:
    """An HS256 web-session JWT from ``AuthService.login`` still authenticates."""
    user_id, email = await seed_password_user(integration_context, _LOGIN_USER_ID)
    await seed_permissions(integration_context, user_id, ["jobs:read"])
    job_id = await _create_job(integration_context, user_id, created_job_ids)
    bundle = await AuthService(integration_context).login(
        LoginPayload(email=email, password=SEED_PASSWORD)
    )
    assert not bundle.access_token.startswith("at_")

    async with _client(admin_app, bundle.access_token) as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == job_id


async def test_combined_app_still_accepts_agent_access_token(
    integration_context: Context, owner_id: str, created_job_ids: list[str]
) -> None:
    """The combined shape keeps resolving ``at_`` (behaviour unchanged)."""
    agent_id, job_id = await _seed_agent_with_job(
        integration_context, owner_id, created_job_ids, scopes=["jobs:read"]
    )
    token = await _mint_agent_access_token(integration_context, agent_id)
    app = create_combined_app(integration_context, ["registry", "admin", "control", "auth"])

    async with _client(app, token) as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 200, resp.text
