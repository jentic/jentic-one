"""Web tests for the auth agents router."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentCredentialBindingRepository,
    AgentRepository,
    EventRepository,
    ExecutionRecordRepository,
    UserRepository,
)
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import AgentPermissionRuleRepository
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, InviteState, StoredCredentialType
from jentic_one.shared.models.events import EventType
from tests.web.auth.conftest import (
    OWNER_EMAIL,
    _build_app,
    _make_agent_token,
    _make_token,
    assert_same_response,
)
from tests.web.conftest import seeded_jwt_user

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
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent.id,
            actor_type="agent",
            permission="test:scope",
            created_by="usr_test",
        )
        await AgentCredentialBindingRepository.bind(
            session, agent_id=agent.id, credential_id="cred-arch-123", created_by="usr_test"
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(
            delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == agent.id)
        )
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == agent.id)
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
        grants = await ActorPermissionGrantRepository.list_for_actor(
            session, archive_target_agent_id
        )
        assert grants == []
        bindings = await AgentCredentialBindingRepository.list_for_agent(
            session, archive_target_agent_id
        )
        assert bindings == []


def test_archive_already_archived(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.delete(f"/agents/{test_agent_id}")
    resp = admin_client.delete(f"/agents/{test_agent_id}")
    assert resp.status_code == 409


async def test_purge_agent_hard_deletes_from_any_status(
    owner_client: TestClient, web_context: Context, archive_target_agent_id: str
) -> None:
    """``?purge=true`` hard-deletes a non-archived agent (204); a repeat is 404."""
    resp = owner_client.delete(f"/agents/{archive_target_agent_id}", params={"purge": "true"})
    assert resp.status_code == 204

    async with web_context.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, archive_target_agent_id) is None
        assert (
            await ActorPermissionGrantRepository.list_for_actor(session, archive_target_agent_id)
            == []
        )
        assert (
            await AgentCredentialBindingRepository.list_for_agent(session, archive_target_agent_id)
            == []
        )

    resp = owner_client.delete(f"/agents/{archive_target_agent_id}", params={"purge": "true"})
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"


def test_purge_archived_agent(admin_client: TestClient, test_agent_id: str) -> None:
    """Purge also accepts an agent that is already archived."""
    assert admin_client.delete(f"/agents/{test_agent_id}").status_code == 204
    resp = admin_client.delete(f"/agents/{test_agent_id}", params={"purge": "true"})
    assert resp.status_code == 204
    assert admin_client.get(f"/agents/{test_agent_id}").status_code == 404


def test_purge_agent_unauthenticated(unauthed_client: TestClient, test_agent_id: str) -> None:
    resp = unauthed_client.delete(f"/agents/{test_agent_id}", params={"purge": "true"})
    assert resp.status_code == 401


async def test_purge_agent_without_write_permission(
    web_context: Context, owner_user_id: str, test_agent_id: str
) -> None:
    """The owner holding only ``agents:read`` is refused (403); the agent stays."""
    token = _make_token(web_context, owner_user_id, OWNER_EMAIL, ["agents:read"])
    with TestClient(
        _build_app(web_context), headers={"Authorization": f"Bearer {token}"}
    ) as client:
        resp = client.delete(f"/agents/{test_agent_id}", params={"purge": "true"})
    assert resp.status_code == 403
    async with web_context.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, test_agent_id) is not None


async def test_purge_foreign_agent_is_not_found(
    web_context: Context, admin_user_id: str, test_agent_id: str
) -> None:
    """A caller with ``agents:write`` who does not own the agent gets the uniform 404."""
    token = _make_token(
        web_context, admin_user_id, "stranger@example.com", ["agents:read", "agents:write"]
    )
    with TestClient(
        _build_app(web_context), headers={"Authorization": f"Bearer {token}"}
    ) as client:
        resp = client.delete(f"/agents/{test_agent_id}", params={"purge": "true"})
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"
    async with web_context.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, test_agent_id) is not None


def test_verbs_on_archived_agent(admin_client: TestClient, test_agent_id: str) -> None:
    admin_client.delete(f"/agents/{test_agent_id}")
    for verb in ("approve", "deny", "disable", "enable"):
        if verb == "deny":
            resp = admin_client.post(f"/agents/{test_agent_id}:{verb}", json={"reason": "test"})
        else:
            resp = admin_client.post(f"/agents/{test_agent_id}:{verb}")
        assert resp.status_code == 409, f"Expected 409 for {verb} on archived agent"


@pytest.fixture()
async def binding_agent_id(web_context: Context, owner_user_id: str) -> AsyncGenerator[str, None]:
    """An active agent for direct credential-binding tests, with full binding cleanup."""
    async with web_context.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="credential-binding-agent",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
            status=ActorStatus.ACTIVE,
        )
    yield agent.id

    async with web_context.admin_db.session() as session:
        await session.execute(
            delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == agent.id)
        )
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == agent.id)
        )
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


@pytest.fixture()
async def control_credential_id(
    web_context: Context, owner_user_id: str
) -> AsyncGenerator[str, None]:
    """A control-DB credential created by the owner user."""
    async with web_context.control_db.session() as session:
        credential = Credential(
            type=StoredCredentialType.API_KEY,
            name="Binding Test Credential",
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            created_by=owner_user_id,
        )
        session.add(credential)
        await session.commit()
        credential_id = credential.id
    yield credential_id

    async with web_context.control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.id == credential_id))
        await session.commit()


def test_credential_binding_lifecycle(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    agent_id = binding_agent_id

    # Bind — 201, enriched with the control-DB name and served API.
    resp = admin_client.post(
        f"/agents/{agent_id}/credentials", json={"credential_id": control_credential_id}
    )
    assert resp.status_code == 201
    binding = resp.json()
    assert binding["credential_id"] == control_credential_id
    assert binding["agent_id"] == agent_id
    assert binding["name"] == "Binding Test Credential"
    assert binding["suspended"] is False
    assert binding["serves"] == [
        {"api_vendor": "stripe", "api_name": "payments", "api_version": "v1"}
    ]

    # List
    resp = admin_client.get(f"/agents/{agent_id}/credentials")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "Binding Test Credential"

    # Duplicate bind -> 409
    resp = admin_client.post(
        f"/agents/{agent_id}/credentials", json={"credential_id": control_credential_id}
    )
    assert resp.status_code == 409
    assert resp.json()["type"] == "credential_binding_conflict"

    # Default unbind -> suspend, binding survives
    resp = admin_client.delete(f"/agents/{agent_id}/credentials/{control_credential_id}")
    assert resp.status_code == 204
    resp = admin_client.get(f"/agents/{agent_id}/credentials")
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["suspended"] is True

    # Resume -> suspended lifted
    resp = admin_client.post(f"/agents/{agent_id}/credentials/{control_credential_id}:resume")
    assert resp.status_code == 200
    assert resp.json()["suspended"] is False

    # Purge -> row gone
    resp = admin_client.delete(
        f"/agents/{agent_id}/credentials/{control_credential_id}", params={"purge": "true"}
    )
    assert resp.status_code == 204
    resp = admin_client.get(f"/agents/{agent_id}/credentials")
    assert resp.json()["data"] == []

    # Unbind nonexistent -> 404
    resp = admin_client.delete(f"/agents/{agent_id}/credentials/{control_credential_id}")
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_binding_not_found"


async def test_credential_binding_last_used(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    """A binding reports when the agent last called through it, overall and per
    API; one never used reads null with no per-API entries."""
    agent_id = binding_agent_id
    resp = admin_client.post(
        f"/agents/{agent_id}/credentials", json={"credential_id": control_credential_id}
    )
    assert resp.status_code == 201
    assert resp.json()["last_used_at"] is None
    assert resp.json()["last_used_by_api"] == []

    latest = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
    async with web_context.admin_db.transaction() as session:
        for started_at, api_name, status in (
            (latest - timedelta(days=2), "payments", "completed"),
            (latest - timedelta(days=1), "payments", "failed"),
            (latest, "billing", "completed"),
        ):
            await ExecutionRecordRepository.create(
                session,
                toolkit_id="tk_test000000000000000000",
                trace_id="abcdef1234567890abcdef12",
                started_at=started_at,
                status=status,
                api_vendor="stripe",
                api_name=api_name,
                created_by=agent_id,
                actor_id=agent_id,
                actor_type="agent",
                credential_id=control_credential_id,
            )
    try:
        resp = admin_client.get(f"/agents/{agent_id}/credentials")
        assert resp.status_code == 200
        (binding,) = resp.json()["data"]
        assert datetime.fromisoformat(binding["last_used_at"]) == latest
        by_api = [
            (u["api_vendor"], u["api_name"], datetime.fromisoformat(u["last_used_at"]))
            for u in binding["last_used_by_api"]
        ]
        # Most recent first; the failed call still counts as use.
        assert by_api == [
            ("stripe", "billing", latest),
            ("stripe", "payments", latest - timedelta(days=1)),
        ]
    finally:
        async with web_context.admin_db.session() as session:
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.actor_id == agent_id)
            )
            await session.commit()


def test_bind_unknown_credential_is_404(admin_client: TestClient, binding_agent_id: str) -> None:
    resp = admin_client.post(
        f"/agents/{binding_agent_id}/credentials", json={"credential_id": "cred_missing"}
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"


@pytest.fixture()
async def foreign_credential_id(web_context: Context) -> AsyncGenerator[str, None]:
    """A control-DB credential created by an unrelated user."""
    async with web_context.control_db.session() as session:
        credential = Credential(
            type=StoredCredentialType.API_KEY,
            name="Someone Elses Credential",
            api_vendor="slack",
            created_by="usr_someone_else",
        )
        session.add(credential)
        await session.commit()
        credential_id = credential.id
    yield credential_id

    async with web_context.control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.id == credential_id))
        await session.commit()


def test_bind_visibility(
    owner_client: TestClient,
    binding_agent_id: str,
    control_credential_id: str,
    foreign_credential_id: str,
) -> None:
    """The bind path checks credential visibility — unlike the toolkit route.

    The owner holds ``agents:write`` but no ``credentials:*`` scope, so they
    can bind a credential they created and get a 404 (not a 403 — existence
    must not leak) for one created by someone else.
    """
    # Own credential -> 201
    resp = owner_client.post(
        f"/agents/{binding_agent_id}/credentials",
        json={"credential_id": control_credential_id},
    )
    assert resp.status_code == 201

    # Foreign credential -> 404, indistinguishable from nonexistent
    resp = owner_client.post(
        f"/agents/{binding_agent_id}/credentials",
        json={"credential_id": foreign_credential_id},
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"


@pytest.mark.parametrize(
    "credential_permissions",
    [["credentials:read"], ["credentials:write"], ["credentials:read", "credentials:write"]],
)
def test_bind_foreign_credential_denied_despite_credentials_scopes(
    web_context: Context,
    owner_user_id: str,
    binding_agent_id: str,
    control_credential_id: str,
    foreign_credential_id: str,
    credential_permissions: list[str],
) -> None:
    """``credentials:*`` does not let a user bind another user's credential (issue #88).

    A binding hands the agent the credential's secret at the broker, so a
    non-admin may bind only credentials they own — whatever credential scopes
    they hold. The foreign credential is a 404 (existence must not leak); the
    caller's own credential still binds.
    """
    token = _make_token(
        web_context,
        owner_user_id,
        OWNER_EMAIL,
        ["agents:read", "agents:write", *credential_permissions],
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.post(
            f"/agents/{binding_agent_id}/credentials",
            json={"credential_id": foreign_credential_id},
        )
        assert resp.status_code == 404
        assert resp.json()["type"] == "credential_not_found"

        resp = client.post(
            f"/agents/{binding_agent_id}/credentials",
            json={"credential_id": control_credential_id},
        )
        assert resp.status_code == 201


def test_admin_can_bind_any_credential(
    admin_client: TestClient, binding_agent_id: str, foreign_credential_id: str
) -> None:
    """``org:admin`` administers every credential, so it may bind one it did not create."""
    resp = admin_client.post(
        f"/agents/{binding_agent_id}/credentials",
        json={"credential_id": foreign_credential_id},
    )
    assert resp.status_code == 201


def test_resume_requires_bind_rights_on_credential(
    owner_client: TestClient,
    admin_client: TestClient,
    binding_agent_id: str,
    control_credential_id: str,
    foreign_credential_id: str,
) -> None:
    """Resuming a suspended binding takes the same credential check as binding.

    The agent owner can see the agent, but a suspended binding to a
    credential they could not bind themselves stays suspended (uniform 404);
    their own credential and ``org:admin`` resume as before.
    """
    agent_id = binding_agent_id
    assert (
        admin_client.post(
            f"/agents/{agent_id}/credentials", json={"credential_id": foreign_credential_id}
        ).status_code
        == 201
    )
    assert (
        admin_client.delete(f"/agents/{agent_id}/credentials/{foreign_credential_id}").status_code
        == 204
    )

    resp = owner_client.post(f"/agents/{agent_id}/credentials/{foreign_credential_id}:resume")
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"
    rows = {
        b["credential_id"]: b
        for b in admin_client.get(f"/agents/{agent_id}/credentials").json()["data"]
    }
    assert rows[foreign_credential_id]["suspended"] is True

    resp = admin_client.post(f"/agents/{agent_id}/credentials/{foreign_credential_id}:resume")
    assert resp.status_code == 200
    assert resp.json()["suspended"] is False

    # The owner's own credential still round-trips suspend -> resume.
    assert (
        owner_client.post(
            f"/agents/{agent_id}/credentials", json={"credential_id": control_credential_id}
        ).status_code
        == 201
    )
    assert (
        owner_client.delete(f"/agents/{agent_id}/credentials/{control_credential_id}").status_code
        == 204
    )
    resp = owner_client.post(f"/agents/{agent_id}/credentials/{control_credential_id}:resume")
    assert resp.status_code == 200
    assert resp.json()["suspended"] is False


@pytest.fixture()
def self_agent_client(
    web_context: Context, binding_agent_id: str, owner_user_id: str
) -> Iterator[TestClient]:
    """The binding agent calling as itself, delegated to its owner's credentials."""
    config = web_context.config.admin.auth
    claims = {
        "sub": binding_agent_id,
        "email": "",
        "actor_type": "agent",
        "parent_actor_id": owner_user_id,
        "permissions": [
            "agents:read",
            "agents:write",
            "owner:agents:read",
            "owner:credentials:read",
        ],
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as tc:
        yield tc


def _suspended_rows(client: TestClient, agent_id: str) -> dict[str, bool]:
    return {
        b["credential_id"]: b["suspended"]
        for b in client.get(f"/agents/{agent_id}/credentials").json()["data"]
    }


def test_agent_cannot_resume_its_own_suspended_binding(
    owner_client: TestClient,
    self_agent_client: TestClient,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    """A suspension on an agent's binding is lifted by its owner, not by the agent.

    The agent could bind its owner's credential, but resuming its own
    suspended binding returns the uniform 404 and the binding stays suspended.
    """
    agent_id = binding_agent_id
    url = f"/agents/{agent_id}/credentials"
    assert owner_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    assert owner_client.delete(f"{url}/{control_credential_id}").status_code == 204

    resp = self_agent_client.post(f"{url}/{control_credential_id}:resume")
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"
    assert _suspended_rows(owner_client, agent_id)[control_credential_id] is True

    resp = owner_client.post(f"{url}/{control_credential_id}:resume")
    assert resp.status_code == 200
    assert resp.json()["suspended"] is False


def _binding_row(client: TestClient, agent_id: str, credential_id: str) -> dict[str, Any]:
    rows = client.get(f"/agents/{agent_id}/credentials").json()["data"]
    return next(b for b in rows if b["credential_id"] == credential_id)


def test_bind_with_expiry_and_renew_binding(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    """A binding takes a future expiry at creation; PATCH moves and clears it."""
    url = f"/agents/{binding_agent_id}/credentials"
    first = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    resp = admin_client.post(
        url, json={"credential_id": control_credential_id, "expires_at": first.isoformat()}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["expired"] is False
    assert body["expired_reason"] is None
    assert datetime.fromisoformat(body["expires_at"]) == first

    later = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)
    resp = admin_client.patch(
        f"{url}/{control_credential_id}", json={"expires_at": later.isoformat()}
    )
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["expires_at"]) == later
    row = _binding_row(admin_client, binding_agent_id, control_credential_id)
    assert datetime.fromisoformat(row["expires_at"]) == later

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"expires_at": None})
    assert resp.status_code == 200
    assert resp.json()["expires_at"] is None
    assert resp.json()["expired"] is False


async def test_binding_audit_records_the_policy_it_granted(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    """An operator can reconstruct the access originally granted, not only later renewals."""
    end = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    resp = admin_client.post(
        f"/agents/{binding_agent_id}/credentials",
        json={
            "credential_id": control_credential_id,
            "expires_at": end.isoformat(),
            "max_idle_period": "P14D",
            "max_quota": 1,
            "after_expiry": "refuse",
        },
    )
    assert resp.status_code == 201

    async with web_context.admin_db.session() as session:
        entry = (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_id == resp.json()["id"],
                    AuditEntry.reason == "bind_credential",
                )
            )
        ).scalar_one()
    granted = entry.after
    assert granted is not None
    assert datetime.fromisoformat(granted["expires_at"]) == end
    assert granted["max_idle_period"] == 14 * 86_400
    assert granted["max_quota"] == 1
    assert granted["after_expiry"] == "refuse"


def test_binding_expiry_must_be_in_the_future(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    resp = admin_client.post(url, json={"credential_id": control_credential_id, "expires_at": past})
    assert resp.status_code == 422
    assert admin_client.get(url).json()["data"] == []

    assert admin_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"expires_at": past})
    assert resp.status_code == 422
    assert _binding_row(admin_client, binding_agent_id, control_credential_id)["expires_at"] is None


def test_renew_missing_binding_is_404(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    resp = admin_client.patch(
        f"/agents/{binding_agent_id}/credentials/{control_credential_id}",
        json={"expires_at": future},
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_binding_not_found"


def test_renew_without_expires_at_reruns_the_grant_and_restarts_the_idle_clock(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    """Leaving ``expires_at`` out runs the binding again for as long as it was granted for."""
    url = f"/agents/{binding_agent_id}/credentials"
    expires = datetime.now(UTC) + timedelta(days=1)
    resp = admin_client.post(
        url,
        json={
            "credential_id": control_credential_id,
            "expires_at": expires.isoformat(),
            "max_idle_period": "P7D",
        },
    )
    assert resp.status_code == 201
    granted = datetime.fromisoformat(resp.json()["renewed_at"])
    length = datetime.fromisoformat(resp.json()["expires_at"]) - granted

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert resp.status_code == 200
    body = resp.json()
    renewed = datetime.fromisoformat(body["renewed_at"])
    assert renewed >= granted
    assert abs(datetime.fromisoformat(body["expires_at"]) - (renewed + length)) < timedelta(
        seconds=1
    )
    assert body["max_idle_period"] == "P7D"
    assert datetime.fromisoformat(body["idle_expires_at"]) == renewed + timedelta(days=7)

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"unknown": 1})
    assert resp.status_code == 422


def test_renew_without_expires_at_keeps_no_end_date_without_a_maximum(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    assert admin_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["expires_at"] is None


def test_bind_with_idle_limit_then_change_and_remove_it(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    resp = admin_client.post(
        url, json={"credential_id": control_credential_id, "max_idle_period": "P2W"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["max_idle_period"] == "P14D"
    assert body["expires_at"] is None
    assert body["expired"] is False
    assert body["idle_expires_at"] is not None

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"max_idle_period": "PT24H"})
    assert resp.status_code == 200
    assert resp.json()["max_idle_period"] == "P1D"

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"max_idle_period": None})
    assert resp.status_code == 200
    assert resp.json()["max_idle_period"] is None
    assert resp.json()["idle_expires_at"] is None


@pytest.mark.parametrize(
    "seconds",
    [
        pytest.param("PT1H", id="under-a-day"),
        pytest.param("PT86399S", id="a-second-short"),
        pytest.param("P1000D", id="over-999-days"),
        pytest.param(1_209_600, id="bare-seconds"),
        pytest.param("14d", id="not-iso"),
        pytest.param("P1M", id="months"),
    ],
)
def test_idle_limit_out_of_range_is_422(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str, seconds: object
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    resp = admin_client.post(
        url, json={"credential_id": control_credential_id, "max_idle_period": seconds}
    )
    assert resp.status_code == 422
    assert admin_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"max_idle_period": seconds})
    assert resp.status_code == 422


def test_idle_warning_is_set_on_bind_and_changed_on_renewal(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    resp = admin_client.post(
        url,
        json={
            "credential_id": control_credential_id,
            "max_idle_period": "P14D",
            "idle_warning": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["idle_warning"] is True

    # Left out, it is unchanged; false turns it off; null is not a value for it.
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert resp.json()["idle_warning"] is True
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"idle_warning": False})
    assert resp.status_code == 200
    assert resp.json()["idle_warning"] is False
    assert (
        _binding_row(admin_client, binding_agent_id, control_credential_id)["idle_warning"] is False
    )


async def test_bindings_ending_soon_lists_warned_bindings_near_their_idle_limit(
    admin_client: TestClient,
    owner_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    ending_soon = "/agents/credentials:ending-soon"
    resp = admin_client.post(
        url,
        json={
            "credential_id": control_credential_id,
            "max_idle_period": "P5D",
            "idle_warning": True,
        },
    )
    assert resp.status_code == 201
    # Five days left: not yet.
    assert admin_client.get(ending_soon).json()["data"] == []

    async def set_binding(**values: object) -> None:
        async with web_context.admin_db.transaction() as session:
            await session.execute(
                update(AgentCredentialBinding)
                .where(AgentCredentialBinding.agent_id == binding_agent_id)
                .where(AgentCredentialBinding.credential_id == control_credential_id)
                .values(**values)
            )

    # Unused for four of its five days: a day left.
    await set_binding(renewed_at=datetime.now(UTC) - timedelta(days=4), last_used_at=None)
    resp = admin_client.get(ending_soon)
    assert resp.status_code == 200
    (row,) = resp.json()["data"]
    assert row["agent_id"] == binding_agent_id
    assert row["agent_name"] == "credential-binding-agent"
    assert row["credential_id"] == control_credential_id
    assert row["name"] == "Binding Test Credential"
    assert row["idle_warning"] is True
    assert row["ending"] == "idle"
    assert row["ends_at"] == row["idle_expires_at"]
    left = datetime.fromisoformat(row["idle_expires_at"]) - datetime.now(UTC)
    assert timedelta(hours=23) < left <= timedelta(days=1)
    # The agent's owner sees it too.
    assert [r["credential_id"] for r in owner_client.get(ending_soon).json()["data"]] == [
        control_credential_id
    ]

    # With the warning off it is not listed, though it still ends on time.
    await set_binding(idle_warning=False)
    assert admin_client.get(ending_soon).json()["data"] == []

    # A renewal restarts the idle clock, which takes it off the list too.
    await set_binding(idle_warning=True)
    assert len(admin_client.get(ending_soon).json()["data"]) == 1
    assert admin_client.patch(f"{url}/{control_credential_id}", json={}).status_code == 200
    assert admin_client.get(ending_soon).json()["data"] == []


def test_after_expiry_defaults_to_hold_and_changes_on_renewal(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    resp = admin_client.post(url, json={"credential_id": control_credential_id})
    assert resp.status_code == 201
    assert resp.json()["after_expiry"] == "hold"

    # Left out, it is unchanged; a value sets it; anything else is refused.
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"after_expiry": "refuse"})
    assert resp.status_code == 200
    assert resp.json()["after_expiry"] == "refuse"
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert resp.json()["after_expiry"] == "refuse"
    assert (
        _binding_row(admin_client, binding_agent_id, control_credential_id)["after_expiry"]
        == "refuse"
    )
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"after_expiry": "ask"})
    assert resp.status_code == 422


async def test_bindings_ending_soon_lists_warned_bindings_near_their_end_date(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    ending_soon = "/agents/credentials:ending-soon"
    end = datetime.now(UTC) + timedelta(days=1)
    resp = admin_client.post(
        url,
        json={
            "credential_id": control_credential_id,
            "expires_at": end.isoformat(),
            "idle_warning": True,
        },
    )
    assert resp.status_code == 201
    # Granted for only a day: too short to warn about.
    assert admin_client.get(ending_soon).json()["data"] == []

    # Granted ten days ago, with a day left.
    async with web_context.admin_db.transaction() as session:
        await session.execute(
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == binding_agent_id)
            .values(renewed_at=datetime.now(UTC) - timedelta(days=9))
        )
    (row,) = admin_client.get(ending_soon).json()["data"]
    assert row["ending"] == "end_date"
    assert datetime.fromisoformat(row["ends_at"]) == datetime.fromisoformat(row["expires_at"])


async def test_single_use_is_set_on_bind_and_restored_on_renewal(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    resp = admin_client.post(url, json={"credential_id": control_credential_id, "max_quota": 1})
    assert resp.status_code == 201
    assert (resp.json()["max_quota"], resp.json()["quota_remaining"]) == (1, 1)
    assert resp.json()["expired"] is False

    # The use is taken: the binding ends, and says why.
    async def use_up() -> None:
        async with web_context.admin_db.transaction() as session:
            await session.execute(
                update(AgentCredentialBinding)
                .where(AgentCredentialBinding.agent_id == binding_agent_id)
                .values(quota_remaining=0)
            )

    await use_up()
    row = _binding_row(admin_client, binding_agent_id, control_credential_id)
    assert (row["expired"], row["expired_reason"], row["quota_remaining"]) == (True, "uses", 0)

    # "Still needed" restores the count.
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert (resp.json()["quota_remaining"], resp.json()["expired"]) == (1, False)
    # A new count replaces it; null removes it.
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"max_quota": 5})
    assert (resp.json()["max_quota"], resp.json()["quota_remaining"]) == (5, 5)
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"max_quota": None})
    assert (resp.json()["max_quota"], resp.json()["quota_remaining"]) == (None, None)


@pytest.mark.parametrize("count", [0, -1, 1.5])
def test_a_use_count_must_be_a_positive_whole_number(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str, count: float
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    resp = admin_client.post(url, json={"credential_id": control_credential_id, "max_quota": count})
    assert resp.status_code == 422


def test_org_grant_limits_are_reported(
    admin_client: TestClient, web_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits = web_context.config.grant_expiry
    monkeypatch.setattr(limits, "default_expires_in", 7 * 86_400)
    monkeypatch.setattr(limits, "max_expires_in", 30 * 86_400)
    monkeypatch.setattr(limits, "single_use_by_default", True)
    resp = admin_client.get("/agents/credentials:policy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] == {"default": "P7D", "max": "P30D"}
    assert body["idle_period"] == {"default": None, "max": None}
    assert body["single_use_by_default"] is True


def test_a_bind_with_no_policy_gets_the_org_default(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = web_context.config.grant_expiry
    monkeypatch.setattr(limits, "default_expires_in", 7 * 86_400)
    monkeypatch.setattr(limits, "single_use_by_default", True)
    resp = admin_client.post(
        f"/agents/{binding_agent_id}/credentials", json={"credential_id": control_credential_id}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    left = datetime.fromisoformat(body["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < left <= timedelta(days=7)
    assert body["max_quota"] == 1


def test_an_org_maximum_refuses_no_end_date_and_longer_ones(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    # Bound before the org set a maximum: no end date.
    assert admin_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    monkeypatch.setattr(web_context.config.grant_expiry, "max_expires_in", 30 * 86_400)

    # A renewal that chooses no end date brings it under the maximum.
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"idle_warning": True})
    assert resp.status_code == 200, resp.text
    left = datetime.fromisoformat(resp.json()["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < left <= timedelta(days=30)
    # One that clears it, or sets it past the maximum, is refused.
    for expires_at in (None, (datetime.now(UTC) + timedelta(days=31)).isoformat()):
        resp = admin_client.patch(f"{url}/{control_credential_id}", json={"expires_at": expires_at})
        assert resp.status_code == 422, resp.text
        assert resp.json()["type"].endswith("grant_policy_limit_exceeded")
    within = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"expires_at": within})
    assert resp.status_code == 200, resp.text


def test_a_renewal_with_no_end_date_takes_the_shorter_of_its_grant_and_the_maximum(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    ninety = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    resp = admin_client.post(
        url, json={"credential_id": control_credential_id, "expires_at": ninety}
    )
    assert resp.status_code == 201, resp.text
    monkeypatch.setattr(web_context.config.grant_expiry, "max_expires_in", 30 * 86_400)

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert resp.status_code == 200, resp.text
    left = datetime.fromisoformat(resp.json()["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < left <= timedelta(days=30)


@pytest.mark.parametrize(
    ("bound_idle", "default", "renewed_idle"),
    [
        pytest.param("P90D", None, "P14D", id="longer-is-capped"),
        pytest.param("P7D", None, "P7D", id="shorter-is-kept"),
        pytest.param(None, None, "P14D", id="none-takes-the-maximum"),
        pytest.param(None, "P10D", "P10D", id="none-takes-the-default"),
    ],
)
def test_a_renewal_that_leaves_the_idle_limit_out_is_held_to_the_maximum(
    admin_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    control_credential_id: str,
    monkeypatch: pytest.MonkeyPatch,
    bound_idle: str | None,
    default: str | None,
    renewed_idle: str,
) -> None:
    """A binding made before the org set an idle maximum can't keep a longer one."""
    url = f"/agents/{binding_agent_id}/credentials"
    body: dict[str, object] = {"credential_id": control_credential_id, "expires_at": None}
    if bound_idle:
        body["max_idle_period"] = bound_idle
    assert admin_client.post(url, json=body).status_code == 201
    limits = web_context.config.grant_expiry
    monkeypatch.setattr(limits, "max_idle_period", 14 * 86_400)
    monkeypatch.setattr(limits, "default_idle_period", 10 * 86_400 if default else None)

    resp = admin_client.patch(f"{url}/{control_credential_id}", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_idle_period"] == renewed_idle


def test_renew_does_not_resume_a_suspended_binding(
    admin_client: TestClient, binding_agent_id: str, control_credential_id: str
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    assert admin_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    assert admin_client.delete(f"{url}/{control_credential_id}").status_code == 204
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    resp = admin_client.patch(f"{url}/{control_credential_id}", json={"expires_at": future})
    assert resp.status_code == 200
    assert resp.json()["suspended"] is True


def test_agent_cannot_renew_its_own_binding(
    owner_client: TestClient,
    self_agent_client: TestClient,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    """Renewal is re-certification: the agent cannot extend its own binding (404)."""
    url = f"/agents/{binding_agent_id}/credentials"
    soon = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
    assert (
        owner_client.post(
            url, json={"credential_id": control_credential_id, "expires_at": soon.isoformat()}
        ).status_code
        == 201
    )
    later = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    resp = self_agent_client.patch(f"{url}/{control_credential_id}", json={"expires_at": later})
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"
    row = _binding_row(owner_client, binding_agent_id, control_credential_id)
    assert datetime.fromisoformat(row["expires_at"]) == soon


def test_owner_without_rights_on_credential_cannot_renew(
    owner_client: TestClient,
    admin_client: TestClient,
    binding_agent_id: str,
    foreign_credential_id: str,
) -> None:
    url = f"/agents/{binding_agent_id}/credentials"
    assert admin_client.post(url, json={"credential_id": foreign_credential_id}).status_code == 201
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    resp = owner_client.patch(f"{url}/{foreign_credential_id}", json={"expires_at": future})
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"


def test_agent_cannot_purge_its_own_binding(
    owner_client: TestClient,
    self_agent_client: TestClient,
    binding_agent_id: str,
    control_credential_id: str,
) -> None:
    """An agent may suspend its own binding but not purge it.

    Purging and re-binding would otherwise drop a suspension the owner set:
    the purge returns the uniform 404, the suspended row survives, and a
    re-bind conflicts with it.
    """
    agent_id = binding_agent_id
    url = f"/agents/{agent_id}/credentials"
    assert owner_client.post(url, json={"credential_id": control_credential_id}).status_code == 201
    # Suspending (narrowing) its own binding stays open to the agent.
    assert self_agent_client.delete(f"{url}/{control_credential_id}").status_code == 204

    resp = self_agent_client.delete(f"{url}/{control_credential_id}", params={"purge": "true"})
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_binding_not_found"
    assert _suspended_rows(owner_client, agent_id)[control_credential_id] is True

    resp = self_agent_client.post(url, json={"credential_id": control_credential_id})
    assert resp.status_code == 409
    assert _suspended_rows(owner_client, agent_id)[control_credential_id] is True

    resp = owner_client.delete(f"{url}/{control_credential_id}", params={"purge": "true"})
    assert resp.status_code == 204


def test_agent_cannot_change_a_sibling_agents_bindings(
    owner_client: TestClient,
    self_agent_client: TestClient,
    binding_agent_id: str,
    test_agent_id: str,
    control_credential_id: str,
) -> None:
    """``owner:agents:read`` shows an agent its owner's other agents, read-only.

    With ``agents:write`` it still cannot bind, suspend, purge or resume a
    binding on a sibling agent: each answers 404 like an agent it cannot
    see, and the sibling's bindings are unchanged. Its own binding of the
    owner's credential still works.
    """
    sibling_url = f"/agents/{test_agent_id}/credentials"
    own_url = f"/agents/{binding_agent_id}/credentials"

    resp = self_agent_client.post(sibling_url, json={"credential_id": control_credential_id})
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"
    assert _suspended_rows(owner_client, test_agent_id) == {}

    assert (
        owner_client.post(sibling_url, json={"credential_id": control_credential_id}).status_code
        == 201
    )
    assert self_agent_client.get(sibling_url).status_code == 200
    for params in ({}, {"purge": "true"}):
        resp = self_agent_client.delete(f"{sibling_url}/{control_credential_id}", params=params)
        assert resp.status_code == 404
        assert resp.json()["type"] == "actor_not_found"
    assert _suspended_rows(owner_client, test_agent_id) == {control_credential_id: False}

    assert owner_client.delete(f"{sibling_url}/{control_credential_id}").status_code == 204
    resp = self_agent_client.post(f"{sibling_url}/{control_credential_id}:resume")
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"
    assert _suspended_rows(owner_client, test_agent_id) == {control_credential_id: True}

    # Its own binding: bind the owner's credential, then suspend it.
    resp = self_agent_client.post(own_url, json={"credential_id": control_credential_id})
    assert resp.status_code == 201
    assert self_agent_client.delete(f"{own_url}/{control_credential_id}").status_code == 204
    assert _suspended_rows(owner_client, binding_agent_id) == {control_credential_id: True}

    # The owner still manages the sibling's binding.
    resp = owner_client.post(f"{sibling_url}/{control_credential_id}:resume")
    assert resp.status_code == 200
    resp = owner_client.delete(f"{sibling_url}/{control_credential_id}", params={"purge": "true"})
    assert resp.status_code == 204


@pytest.fixture()
def delegated_sibling_client(
    web_context: Context, binding_agent_id: str, owner_user_id: str
) -> Iterator[TestClient]:
    """The binding agent with the default delegation scopes and no ``agents:read``."""
    config = web_context.config.admin.auth
    claims = {
        "sub": binding_agent_id,
        "email": "",
        "actor_type": "agent",
        "parent_actor_id": owner_user_id,
        "permissions": ["owner:agents:read", "owner:credentials:read"],
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as tc:
        yield tc


def _normalised_problem(body: dict[str, object], agent_id: str) -> dict[str, object]:
    """A problem body with the requested agent id and per-request ids blanked."""
    text = json.dumps(body).replace(agent_id, "<agent_id>")
    normalised: dict[str, object] = json.loads(text)
    for key in ("request_id", "trace_id"):
        normalised.pop(key, None)
    return normalised


@pytest.mark.parametrize("suffix", ["", "/credentials"])
def test_sibling_agent_read_is_indistinguishable_from_unknown_id(
    owner_client: TestClient,
    delegated_sibling_client: TestClient,
    binding_agent_id: str,
    test_agent_id: str,
    suffix: str,
) -> None:
    """An agent that is not the target, its owner, or org:admin gets the unknown-id 404.

    ``owner:agents:read`` without ``agents:read`` does not reveal that a
    sibling agent exists: the problem body matches a nonexistent id once the
    ids are normalised. The agent itself and its owner still read it.
    """
    sibling = delegated_sibling_client.get(f"/agents/{test_agent_id}{suffix}")
    unknown_id = "agnt_does_not_exist"
    unknown = delegated_sibling_client.get(f"/agents/{unknown_id}{suffix}")
    assert sibling.status_code == unknown.status_code == 404
    assert sibling.headers["content-type"] == unknown.headers["content-type"]
    assert _normalised_problem(sibling.json(), test_agent_id) == _normalised_problem(
        unknown.json(), unknown_id
    )
    assert sibling.json()["type"] == "actor_not_found"

    assert delegated_sibling_client.get(f"/agents/{binding_agent_id}{suffix}").status_code == 200
    assert owner_client.get(f"/agents/{test_agent_id}{suffix}").status_code == 200


@pytest.mark.parametrize("body", [{}, {"description": "changed"}], ids=["empty", "non-empty"])
def test_sibling_agent_patch_is_indistinguishable_from_unknown_id(
    web_context: Context,
    owner_client: TestClient,
    binding_agent_id: str,
    owner_user_id: str,
    test_agent_id: str,
    body: dict[str, str],
) -> None:
    """A sibling holding ``agents:write`` gets the unknown-id 404 from ``PATCH``.

    With ``owner:agents:read`` but not ``agents:read`` it cannot read the
    sibling, so an empty body must not return the sibling's profile either:
    both bodies answer exactly like a nonexistent id. The owner's empty
    ``PATCH`` still returns the agent.
    """
    token = _make_agent_token(
        web_context, binding_agent_id, owner_user_id, ["owner:agents:read", "agents:write"]
    )
    unknown_id = "agnt_does_not_exist"
    with TestClient(
        _build_app(web_context), headers={"Authorization": f"Bearer {token}"}
    ) as sibling_client:
        sibling = sibling_client.patch(f"/agents/{test_agent_id}", json=body)
        unknown = sibling_client.patch(f"/agents/{unknown_id}", json=body)
    assert sibling.status_code == 404, sibling.text
    assert sibling.json()["type"] == "actor_not_found"
    assert_same_response(sibling, test_agent_id, unknown, unknown_id)

    own = owner_client.patch(f"/agents/{test_agent_id}", json={})
    assert own.status_code == 200, own.text
    assert own.json()["id"] == test_agent_id
    assert own.json()["description"] == "Test agent for web tests"


@pytest.mark.asyncio
async def test_purge_drops_inline_rules_so_rebind_starts_default_deny(
    owner_client: TestClient,
    web_context: Context,
    binding_agent_id: str,
    test_agent_id: str,
    control_credential_id: str,
) -> None:
    """Purging a binding deletes the pair's inline rules along with the row.

    A re-bind of the same pair therefore starts with no rule set and no
    inline rules (default deny) — it cannot pick up rules that were dormant
    under a rule set attached to the purged binding. Another agent's rules on
    the same credential are untouched.
    """
    agent_id = binding_agent_id
    bind_url = f"/agents/{agent_id}/credentials"
    assert (
        owner_client.post(bind_url, json={"credential_id": control_credential_id}).status_code
        == 201
    )
    wide: list[dict[str, object]] = [{"effect": "allow", "methods": ["GET", "POST"], "path": ".*"}]
    async with web_context.control_db.transaction() as session:
        for aid in (agent_id, test_agent_id):
            await AgentPermissionRuleRepository.replace_user_rules(
                session, aid, control_credential_id, wide, created_by="usr_test"
            )
    async with web_context.admin_db.transaction() as session:
        await session.execute(
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == control_credential_id)
            .values(rule_set_id="prs_attached_set")
        )

    try:
        resp = owner_client.delete(f"{bind_url}/{control_credential_id}", params={"purge": "true"})
        assert resp.status_code == 204

        async with web_context.control_db.session() as session:
            assert (
                await AgentPermissionRuleRepository.list_rules(
                    session, agent_id, control_credential_id
                )
                == []
            )
            others = await AgentPermissionRuleRepository.list_rules(
                session, test_agent_id, control_credential_id
            )
            assert len(others) == 1

        resp = owner_client.post(bind_url, json={"credential_id": control_credential_id})
        assert resp.status_code == 201
        assert resp.json()["rule_set_id"] is None
        async with web_context.control_db.session() as session:
            assert (
                await AgentPermissionRuleRepository.list_rules(
                    session, agent_id, control_credential_id
                )
                == []
            )
    finally:
        async with web_context.control_db.transaction() as session:
            await AgentPermissionRuleRepository.replace_user_rules(
                session, test_agent_id, control_credential_id, [], created_by="usr_test"
            )


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
        event_id = await EventRepository.create(
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
    yield event_id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Event).where(Event.id == event_id))
        await session.commit()


async def test_approve_emits_decision_event(
    admin_client: TestClient,
    web_context: Context,
    dcr_agent_id: str,
    self_registered_alert_id: str,
) -> None:
    """Approving emits the decision event; the self-registered event stays as
    append-only history.

    Also pins the decision event's payload contract — `data.agent_id` is what
    lets the UI deep-link the rail row to the agent page (the top-level actor
    on the decision event is the deciding user, not the agent).
    """
    resp = admin_client.post(f"/agents/{dcr_agent_id}:approve")
    assert resp.status_code == 200

    async with web_context.admin_db.session() as session:
        alert = await EventRepository.get_by_id(session, self_registered_alert_id)
        assert alert is not None
        assert alert.requires_action is True

        decisions = await EventRepository.list_all(
            session, event_type=[EventType.AGENT_REGISTRATION_APPROVED]
        )
        decision = next(e for e in decisions if e.data.get("agent_id") == dcr_agent_id)
        assert decision.data["agent_name"] == "dcr-self-registered"
        assert decision.actor_type == "user"
        await session.execute(delete(Event).where(Event.id == decision.id))
        await session.commit()


async def test_deny_emits_decision_event(
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
        assert alert.requires_action is True

        decisions = await EventRepository.list_all(
            session, event_type=[EventType.AGENT_REGISTRATION_DENIED]
        )
        decision = next(e for e in decisions if e.data.get("agent_id") == dcr_agent_id)
        assert decision.data["agent_name"] == "dcr-self-registered"
        await session.execute(delete(Event).where(Event.id == decision.id))
        await session.commit()


async def test_approve_leaves_other_agents_alerts_untouched(
    admin_client: TestClient,
    web_context: Context,
    dcr_agent_id: str,
    self_registered_alert_id: str,
) -> None:
    """Deciding one agent never touches another agent's self-registered event.

    Two agents awaiting review is the normal fleet-onboarding case; both events
    stay as append-only history regardless of which agent is decided.
    """
    async with web_context.admin_db.transaction() as session:
        other_id = await EventRepository.create(
            session,
            type=EventType.AGENT_SELF_REGISTERED,
            severity="info",
            summary="Agent 'other-agent' self-registered and awaits approval",
            requires_action=True,
            data={"agent_id": "agnt_other_pending", "agent_name": "other-agent"},
            created_by="dcr",
            actor_id="agnt_other_pending",
            actor_type="agent",
        )

    try:
        resp = admin_client.post(f"/agents/{dcr_agent_id}:approve")
        assert resp.status_code == 200

        async with web_context.admin_db.session() as session:
            decided = await EventRepository.get_by_id(session, self_registered_alert_id)
            assert decided is not None
            assert decided.requires_action is True

            untouched = await EventRepository.get_by_id(session, other_id)
            assert untouched is not None
            assert untouched.requires_action is True
    finally:
        async with web_context.admin_db.session() as session:
            await session.execute(delete(Event).where(Event.id == other_id))
            await session.execute(
                delete(Event).where(Event.type == EventType.AGENT_REGISTRATION_APPROVED)
            )
            await session.commit()


@pytest.fixture()
async def _rotation_user(web_context: Context) -> AsyncGenerator[str, None]:
    async with seeded_jwt_user(web_context, "user-needs-rotation") as user_id:
        yield user_id


@pytest.mark.usefixtures("_rotation_user")
def test_password_rotation_required(web_context: Context) -> None:
    """Tokens with must_change_password=True get 403 on permission-gated endpoints."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "user-needs-rotation",
        "email": "rotation@test.local",
        "actor_type": "user",
        "permissions": ["org:admin"],
        "must_change_password": True,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)

    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/agents")
        assert resp.status_code == 403
        assert resp.json()["type"] == "password_rotation_required"


# --- Ownership claim (POST /agents/{id}:claim) -----------------------------------

_CLAIM_TOKEN = "claim-web-secret-abc123"


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture()
async def claimable_agent_id(web_context: Context) -> AsyncGenerator[str, None]:
    """A self-registered (unowned, pending) agent carrying a valid claim token."""
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        agent = await AgentRepository.create_dcr(
            session,
            name="claimable-agent",
            jwks={"keys": []},
            rat_hash="unused",
            rat_expires_at=datetime.now(UTC) + timedelta(minutes=10),
            claim_token_hash=_sha256(_CLAIM_TOKEN),
            claim_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    yield agent.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


@pytest.fixture()
async def member_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    """A real user row with NO agent permissions — a valid FK target for owner_id."""
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email="auth-web-test-member@test.local",
            first_name="Member",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.owner_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
def member_client(web_context: Context, member_user_id: str) -> Iterator[TestClient]:
    """A logged-in user with NO agent permissions — the claim token is the proof."""
    config = web_context.config.admin.auth
    claims = {
        "sub": member_user_id,
        "email": "auth-web-test-member@test.local",
        "actor_type": "user",
        "permissions": [],
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as tc:
        yield tc


def test_claim_agent_sets_owner_to_caller(
    member_client: TestClient, member_user_id: str, claimable_agent_id: str
) -> None:
    """A member with a valid token becomes the owner — no agents:write needed."""
    resp = member_client.post(f"/agents/{claimable_agent_id}:claim", json={"token": _CLAIM_TOKEN})
    assert resp.status_code == 200
    assert resp.json()["owner_id"] == member_user_id


def test_claim_agent_is_single_use(member_client: TestClient, claimable_agent_id: str) -> None:
    """The token is consumed on first claim; a replay fails (already owned)."""
    first = member_client.post(f"/agents/{claimable_agent_id}:claim", json={"token": _CLAIM_TOKEN})
    assert first.status_code == 200
    replay = member_client.post(f"/agents/{claimable_agent_id}:claim", json={"token": _CLAIM_TOKEN})
    assert replay.status_code == 409
    assert replay.json()["type"] == "agent_already_owned"


def test_claim_agent_wrong_token(member_client: TestClient, claimable_agent_id: str) -> None:
    resp = member_client.post(
        f"/agents/{claimable_agent_id}:claim", json={"token": "not-the-token"}
    )
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_claim_token"


def test_claim_agent_non_user_actor_forbidden(
    web_context: Context,
    claimable_agent_id: str,
    binding_agent_id: str,
    owner_user_id: str,
) -> None:
    """A non-user actor (agent) with a valid token is refused (403) — owner_id is
    a FK to users.id, so only a human user can own an agent. The ``:claim``
    endpoint's ``require_actor_type=USER`` gate rejects it at the boundary (type
    ``forbidden``) before the service runs; the service re-checks as
    defense-in-depth. Guards against a self-registered agent claiming itself into
    an integrity error / 500."""
    config = web_context.config.admin.auth
    claims = {
        "sub": binding_agent_id,
        "email": "",
        "actor_type": "agent",
        "parent_actor_id": owner_user_id,
        "permissions": [],
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as tc:
        resp = tc.post(f"/agents/{claimable_agent_id}:claim", json={"token": _CLAIM_TOKEN})
    assert resp.status_code == 403
    assert resp.json()["type"] == "forbidden"


def test_claim_agent_unauthenticated(unauthed_client: TestClient, claimable_agent_id: str) -> None:
    resp = unauthed_client.post(f"/agents/{claimable_agent_id}:claim", json={"token": _CLAIM_TOKEN})
    assert resp.status_code == 401


#: Cursors that base64-decode to JSON of the wrong shape (#1956).
_WRONG_SHAPE_CURSORS = [
    base64.b64encode(raw.encode()).decode()
    for raw in ("null", "[1]", '{"t": 1, "id": "x"}', '{"t": "2024-01-01", "id": 1}')
]


@pytest.mark.parametrize("cursor", _WRONG_SHAPE_CURSORS)
def test_list_agents_wrong_shape_cursor_returns_400(admin_client: TestClient, cursor: str) -> None:
    """A tampered cursor on the auth surface is a 400 ``invalid_cursor``, never a 500."""
    resp = admin_client.get("/agents", params={"cursor": cursor})
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_cursor"
