"""Web tests for the actors router: full listing vs. by-id lookup."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import AgentRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.admin.web.routers.actors import MAX_LOOKUP_ID_LENGTH, MAX_LOOKUP_IDS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus

pytestmark = pytest.mark.integration


def _member_headers(ctx: Context, user_id: str) -> dict[str, str]:
    """Bearer header for a signed-in member holding neither users:read nor org:admin."""
    config = ctx.config.admin.auth
    claims = {
        "sub": user_id,
        "email": "web-managed@test.local",
        "actor_type": "user",
        "permissions": ["agents:read", "events:read"],
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def agent_id(web_context: Context, managed_user_id: str) -> AsyncGenerator[str, None]:
    async with web_context.admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="lookup-web-agent",
            owner_id=managed_user_id,
            registered_by=managed_user_id,
            created_by=managed_user_id,
            status=ActorStatus.ACTIVE,
        )
        await session.commit()
        created = agent.id
    yield created
    async with web_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == created))
        await session.commit()


@pytest.fixture()
async def outsider_agent_id(web_context: Context, admin_user_id: str) -> AsyncGenerator[str, None]:
    """An agent owned by someone other than the member."""
    async with web_context.admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="lookup-outsider-agent",
            owner_id=admin_user_id,
            registered_by=admin_user_id,
            created_by=admin_user_id,
            status=ActorStatus.ACTIVE,
        )
        await session.commit()
        created = agent.id
    yield created
    async with web_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == created))
        await session.commit()


def test_member_does_not_resolve_others_agents(
    unauthed_client: TestClient,
    authed_client: TestClient,
    web_context: Context,
    managed_user_id: str,
    outsider_agent_id: str,
) -> None:
    params = {"id": outsider_agent_id}
    resp = unauthed_client.get(
        "/actors/lookup", params=params, headers=_member_headers(web_context, managed_user_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"data": []}
    # An admin resolves the same id.
    resp = authed_client.get("/actors/lookup", params=params)
    assert [a["id"] for a in resp.json()["data"]] == [outsider_agent_id]


def test_lookup_rejects_overlong_id(
    unauthed_client: TestClient, web_context: Context, managed_user_id: str
) -> None:
    headers = _member_headers(web_context, managed_user_id)
    resp = unauthed_client.get(
        "/actors/lookup", params={"id": "u" * (MAX_LOOKUP_ID_LENGTH + 1)}, headers=headers
    )
    assert resp.status_code == 422
    resp = unauthed_client.get(
        "/actors/lookup", params={"id": "u" * MAX_LOOKUP_ID_LENGTH}, headers=headers
    )
    assert resp.status_code == 200


def test_member_resolves_names_by_id(
    unauthed_client: TestClient,
    web_context: Context,
    admin_user_id: str,
    managed_user_id: str,
    agent_id: str,
) -> None:
    resp = unauthed_client.get(
        "/actors/lookup",
        params=[
            ("id", admin_user_id),
            ("id", agent_id),
            ("id", "usr_does_not_exist"),
            ("id", admin_user_id),
        ],
        headers=_member_headers(web_context, managed_user_id),
    )
    assert resp.status_code == 200, resp.text
    data = {entry["id"]: entry for entry in resp.json()["data"]}
    assert set(data) == {admin_user_id, agent_id}
    assert data[admin_user_id] == {
        "id": admin_user_id,
        "actor_type": "user",
        "name": "Web Admin",
        "active": True,
    }
    assert data[agent_id] == {
        "id": agent_id,
        "actor_type": "agent",
        "name": "lookup-web-agent",
        "active": True,
    }


def test_member_cannot_list_full_directory(
    unauthed_client: TestClient, web_context: Context, managed_user_id: str
) -> None:
    resp = unauthed_client.get("/actors", headers=_member_headers(web_context, managed_user_id))
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_admin_can_still_list_full_directory(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.get("/actors")
    assert resp.status_code == 200
    assert admin_user_id in {a["id"] for a in resp.json()["data"]}


def test_lookup_requires_authentication(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/actors/lookup", params={"id": "usr_any"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("count", [0, MAX_LOOKUP_IDS + 1])
def test_lookup_enforces_id_count(
    unauthed_client: TestClient, web_context: Context, managed_user_id: str, count: int
) -> None:
    resp = unauthed_client.get(
        "/actors/lookup",
        params=[("id", f"usr_{i}") for i in range(count)],
        headers=_member_headers(web_context, managed_user_id),
    )
    assert resp.status_code == 422


def test_lookup_accepts_max_id_count(
    unauthed_client: TestClient, web_context: Context, managed_user_id: str
) -> None:
    resp = unauthed_client.get(
        "/actors/lookup",
        params=[("id", f"usr_{i}") for i in range(MAX_LOOKUP_IDS)],
        headers=_member_headers(web_context, managed_user_id),
    )
    assert resp.status_code == 200
    assert resp.json() == {"data": []}
