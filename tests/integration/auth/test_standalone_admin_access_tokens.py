"""Integration tests: a standalone admin process accepts opaque ``at_`` tokens (#1445).

In a split deployment the admin surface runs without the auth surface in its
process. An OAuth/MCP agent still polls the broker's async ``_links.self``
(``GET /jobs/{id}``) on admin with its opaque ``at_`` access token, so the
standalone admin app must resolve ``at_`` against the admin DB's
``access_tokens`` table exactly as the combined app does — and fail closed
(401) for an unknown or revoked token.

The app is the real standalone admin factory (``admin.web.app.create_app``),
driven in-process over ASGI so it runs on the test's event loop (SQLite and
Postgres alike).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import JobRepository
from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, JobKind, JobStatus
from jentic_one.shared.web.app_factory import create_combined_app
from tests.integration.auth.seeds import seed_agent, seed_user

pytestmark = pytest.mark.integration

_OWNER_ID = "usr_split_admin_owner"


@pytest.fixture()
async def agent_job(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[tuple[str, str], None]:
    """An active agent holding ``jobs:read`` and a completed job it created.

    The seeded owner and agent are removed by the shared ``clean_grants`` fixture.
    """
    owner_id = await seed_user(integration_context, _OWNER_ID)
    agent_id = await seed_agent(integration_context, owner_id=owner_id, scopes=["jobs:read"])
    async with integration_context.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.COMPLETED, created_by=agent_id
        )
        await session.commit()
        job_id = job.id
    yield agent_id, job_id
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Job).where(Job.id == job_id))
        await session.commit()


def _client(app: FastAPI, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _mint_access_token(ctx: Context, agent_id: str) -> str:
    access, _refresh = await TokenService(ctx).issue_pair(agent_id, ActorType.AGENT, ["jobs:read"])
    assert access.startswith("at_")
    return access


async def test_standalone_admin_accepts_agent_access_token(
    integration_context: Context, agent_job: tuple[str, str]
) -> None:
    agent_id, job_id = agent_job
    token = await _mint_access_token(integration_context, agent_id)

    async with _client(create_admin_app(integration_context), token) as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == job_id


async def test_standalone_admin_rejects_unknown_access_token(
    integration_context: Context, agent_job: tuple[str, str]
) -> None:
    _agent_id, job_id = agent_job

    async with _client(create_admin_app(integration_context), "at_not-a-real-token") as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 401, resp.text


async def test_standalone_admin_rejects_revoked_access_token(
    integration_context: Context, agent_job: tuple[str, str]
) -> None:
    agent_id, job_id = agent_job
    token = await _mint_access_token(integration_context, agent_id)
    await TokenService(integration_context).revoke(
        token, identity=Identity(sub=agent_id, actor_type=ActorType.AGENT)
    )

    async with _client(create_admin_app(integration_context), token) as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 401, resp.text


async def test_combined_app_still_accepts_agent_access_token(
    integration_context: Context, agent_job: tuple[str, str]
) -> None:
    """The combined shape keeps resolving ``at_`` (behaviour unchanged)."""
    agent_id, job_id = agent_job
    token = await _mint_access_token(integration_context, agent_id)
    app = create_combined_app(integration_context, ["registry", "admin", "control", "auth"])

    async with _client(app, token) as client:
        resp = await client.get(f"/jobs/{job_id}")

    assert resp.status_code == 200, resp.text
