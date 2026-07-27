"""Web tests for the auth agents router."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import ActorScopeGrantRepository, AgentRepository, EventRepository
from jentic_one.admin.repos.agent_toolkit_binding_repo import AgentToolkitBindingRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType
from tests.web.auth.conftest import _build_app

pytestmark = pytest.mark.integration


def test_list_agents_admin(admin_client: TestClient, test_agent_id: str) -> None:
    resp = admin_client.get("/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data
    assert "next_cursor" in data
    ids = [a["id"] for a in data["data"]]
    assert test_agent_id in ids


def test_list_agents_owner_scoped(owner_client: TestClient, test_agent_id: str) -> None:
    resp = owner_client.get("/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "has_more" in data
    assert "next_cursor" in data
    ids = [a["id"] for a in data["data"]]
    assert test_agent_id in ids


def test_list_agents_unauthenticated(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/agents")
    assert resp.status_code == 401


def test_get_agent(admin_client: TestClient, test_agent_id: str) -> None:
    resp = admin_client.get(f"/agents/{test_agent_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == test_agent_id
    assert data["status"] == "pending"


def test_get_agent_not_found(admin_client: TestClient) -> None:
    resp = admin_client.get("/agents/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"


def test_approve_agent(admin_client: TestClient, test_agent_id: str) -> None:
    resp = admin_client.post(f"/agents/{test_agent_id}:approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["approved_by"] is not None
    assert data["approved_at"] is not None


def test_approve_agent_invalid_state(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.post(f"/agents/{test_agent_id}:approve")
    resp = admin_client.post(f"/agents/{test_agent_id}:approve")
    assert resp.status_code == 409
    assert resp.json()["type"] == "invalid_transition"


@pytest.fixture()
async def deny_target_agent_id(
    web_context: Context, owner_user_id: str
) -> AsyncGenerator[str, None]:
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="deny-target",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


def test_deny_agent(admin_client: TestClient, deny_target_agent_id: str) -> None:
    resp = admin_client.post(
        f"/agents/{deny_target_agent_id}:deny", json={"reason": "Not approved"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["denial_reason"] == "Not approved"


def test_disable_agent(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.post(f"/agents/{test_agent_id}:approve")
    resp = admin_client.post(f"/agents/{test_agent_id}:disable")
    assert resp.status_code == 204


def test_enable_agent(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.post(f"/agents/{test_agent_id}:approve")
    admin_client.post(f"/agents/{test_agent_id}:disable")
    resp = admin_client.post(f"/agents/{test_agent_id}:enable")
    assert resp.status_code == 204


@pytest.fixture()
async def archive_target_agent_id(
    web_context: Context, owner_user_id: str
) -> AsyncGenerator[str, None]:
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="archive-target",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
        )
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=agent.id,
            actor_type="agent",
            scope="test:scope",
            created_by="usr_test",
        )
        await AgentToolkitBindingRepository.bind(
            session, agent_id=agent.id, toolkit_id="tk-123", created_by="usr_test"
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(ActorScopeGrant).where(ActorScopeGrant.actor_id == agent.id))
        await session.execute(
            delete(AgentToolkitBinding).where(AgentToolkitBinding.agent_id == agent.id)
        )
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


async def test_archive_agent(
    admin_client: TestClient, web_context: Context, archive_target_agent_id: str
) -> None:
    resp = admin_client.delete(f"/agents/{archive_target_agent_id}")
    assert resp.status_code == 204

    async with web_context.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, archive_target_agent_id)
        assert agent is not None
        assert agent.status == "archived"
        grants = await ActorScopeGrantRepository.list_for_actor(session, archive_target_agent_id)
        assert grants == []
        bindings = await AgentToolkitBindingRepository.list_for_agent(
            session, archive_target_agent_id
        )
        assert bindings == []


def test_archive_already_archived(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.delete(f"/agents/{test_agent_id}")
    resp = admin_client.delete(f"/agents/{test_agent_id}")
    assert resp.status_code == 409


def test_verbs_on_archived_agent(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.delete(f"/agents/{test_agent_id}")
    for verb in ("approve", "deny", "disable", "enable"):
        if verb == "deny":
            resp = admin_client.post(f"/agents/{test_agent_id}:{verb}", json={"reason": "test"})
        else:
            resp = admin_client.post(f"/agents/{test_agent_id}:{verb}")
        assert resp.status_code == 409, f"Expected 409 for {verb} on archived agent"


@pytest.fixture()
async def toolkit_agent_id(web_context: Context, owner_user_id: str) -> AsyncGenerator[str, None]:
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="toolkit-agent",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(ActorScopeGrant).where(ActorScopeGrant.actor_id == agent.id))
        await session.execute(
            delete(AgentToolkitBinding).where(AgentToolkitBinding.agent_id == agent.id)
        )
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


def test_toolkit_crud(admin_client: TestClient, toolkit_agent_id: str) -> None:
    agent_id = toolkit_agent_id

    # Bind
    resp = admin_client.post(f"/agents/{agent_id}/toolkits", json={"toolkit_id": "tk-abc"})
    assert resp.status_code == 201
    binding = resp.json()
    assert binding["toolkit_id"] == "tk-abc"
    assert binding["agent_id"] == agent_id

    # List
    resp = admin_client.get(f"/agents/{agent_id}/toolkits")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    # Duplicate bind -> 409
    resp = admin_client.post(f"/agents/{agent_id}/toolkits", json={"toolkit_id": "tk-abc"})
    assert resp.status_code == 409

    # Unbind
    resp = admin_client.delete(f"/agents/{agent_id}/toolkits/tk-abc")
    assert resp.status_code == 204

    # Unbind nonexistent -> 404
    resp = admin_client.delete(f"/agents/{agent_id}/toolkits/tk-abc")
    assert resp.status_code == 404


@pytest.fixture()
async def dcr_agent_id(web_context: Context) -> AsyncGenerator[str, None]:
    """A self-registered (DCR) agent with no human owner (owner_id is NULL)."""
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create_dcr(
            session,
            name="dcr-self-registered",
            jwks={"keys": []},
            rat_hash="x" * 64,
            rat_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


def test_list_agents_includes_dcr_agent(admin_client: TestClient, dcr_agent_id: str) -> None:
    """Regression: listing a self-registered agent (owner_id=None) must not 500.

    The agents table allows a NULL owner_id for DCR self-registration, so the
    AgentView / AgentResponse schemas must treat owner_id as optional.
    """
    resp = admin_client.get("/agents")
    assert resp.status_code == 200
    agents = {a["id"]: a for a in resp.json()["data"]}
    assert dcr_agent_id in agents
    assert agents[dcr_agent_id]["owner_id"] is None
    assert agents[dcr_agent_id]["registered_by"] == "self"


def test_get_dcr_agent(admin_client: TestClient, dcr_agent_id: str) -> None:
    resp = admin_client.get(f"/agents/{dcr_agent_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == dcr_agent_id
    assert data["owner_id"] is None


async def test_rat_cleared_after_approval(
    admin_client: TestClient, web_context: Context, dcr_agent_id: str
) -> None:
    """Approval must invalidate the RAT (RFC 7592 single-use credential)."""
    resp = admin_client.post(f"/agents/{dcr_agent_id}:approve")
    assert resp.status_code == 200

    async with web_context.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, dcr_agent_id)
        assert agent is not None
        assert agent.registration_access_token_hash is None
        assert agent.rat_expires_at is None


@pytest.fixture()
async def self_registered_alert_id(
    web_context: Context, dcr_agent_id: str
) -> AsyncGenerator[str, None]:
    """The actionable `agent.self_registered` event DCR files for the agent."""
    async with web_context.admin_db.transaction() as session:
        event = await EventRepository.create(
            session,
            type=EventType.AGENT_SELF_REGISTERED,
            severity="info",
            summary="Agent 'dcr-self-registered' self-registered and awaits approval",
            requires_action=True,
            data={"agent_id": dcr_agent_id, "agent_name": "dcr-self-registered"},
            created_by="dcr",
            actor_id=dcr_agent_id,
            actor_type="agent",
        )
    yield event.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Event).where(Event.id == event.id))
        await session.commit()


async def test_approve_settles_self_registered_alert(
    admin_client: TestClient,
    web_context: Context,
    dcr_agent_id: str,
    self_registered_alert_id: str,
) -> None:
    """Approving IS the review: the pending alert must not stay actionable.

    Also pins the decision event's payload contract — `data.agent_id` is what
    lets the UI deep-link the rail row to the agent page (the top-level actor
    on the decision event is the deciding user, not the agent).
    """
    resp = admin_client.post(f"/agents/{dcr_agent_id}:approve")
    assert resp.status_code == 200

    async with web_context.admin_db.session() as session:
        alert = await EventRepository.get_by_id(session, self_registered_alert_id)
        assert alert is not None
        assert alert.acknowledged is True
        assert alert.acknowledged_by is not None

        decisions = await EventRepository.list_all(
            session, event_type=[EventType.AGENT_REGISTRATION_APPROVED]
        )
        decision = next(e for e in decisions if e.data.get("agent_id") == dcr_agent_id)
        assert decision.data["agent_name"] == "dcr-self-registered"
        assert decision.actor_type == "user"
        await session.execute(delete(Event).where(Event.id == decision.id))
        await session.commit()


async def test_deny_settles_self_registered_alert(
    admin_client: TestClient,
    web_context: Context,
    dcr_agent_id: str,
    self_registered_alert_id: str,
) -> None:
    resp = admin_client.post(f"/agents/{dcr_agent_id}:deny", json={"reason": "nope"})
    assert resp.status_code == 200

    async with web_context.admin_db.session() as session:
        alert = await EventRepository.get_by_id(session, self_registered_alert_id)
        assert alert is not None
        assert alert.acknowledged is True

        decisions = await EventRepository.list_all(
            session, event_type=[EventType.AGENT_REGISTRATION_DENIED]
        )
        decision = next(e for e in decisions if e.data.get("agent_id") == dcr_agent_id)
        assert decision.data["agent_name"] == "dcr-self-registered"
        await session.execute(delete(Event).where(Event.id == decision.id))
        await session.commit()


def test_password_rotation_required(web_context: Context) -> None:
    """Tokens with must_change_password=True get 403 on permission-gated endpoints."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "user-needs-rotation",
        "email": "rotation@test.local",
        "permissions": ["org:admin"],
        "must_change_password": True,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)

    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/agents")
        assert resp.status_code == 403
        assert resp.json()["type"] == "password_rotation_required"
