"""Integration tests: scope ceiling and owner scoping on agent write routes.

- ``POST /agents`` and ``PUT /agents/{id}/scopes`` enforce the agent scope
  ceiling: a caller without ``org:admin`` may grant only scopes it holds (or
  the default agent baseline), never ``org:admin`` / ``agents:write``, and no
  scope outside the permission catalogue.
- ``PUT …/scopes``, ``PATCH``, ``:disable``, ``:enable`` and ``DELETE`` are
  owner-or-``org:admin`` with a uniform 404; an ``owner_id`` change is
  ``org:admin``-only.
- ``:approve`` / ``:deny`` stay open to any approver holding ``agents:write``,
  but approving applies the approver's ceiling to the scopes a
  self-registration requested (they become live on approval).

Router, service, repositories and DB are real; the only shim is the identity
dependency override (the same pattern as ``test_oauth_grant_transfer.py``).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import ActorScopeGrantRepository, AgentRepository, AuditRepository
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.services.registration_service import RegistrationService
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.shared.audit import AuditAction, AuditTargetType
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES
from jentic_one.shared.web.deps import resolve_identity
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

OWNER = "usr_authz_owner"
OTHER = "usr_authz_other"
ADMIN = "usr_authz_admin"
_WRITER_PERMS = ["agents:read", "agents:write"]
_DCR_JWKS: dict[str, Any] = {
    "keys": [{"kty": "OKP", "crv": "Ed25519", "x": "dGVzdC1wdWJsaWMta2V5LWJhc2U2NA", "kid": "k1"}]
}


@pytest.fixture()
async def users(integration_context: Context, clean_grants: None) -> AsyncGenerator[None, None]:
    """Seed the three callers; drop every agent they own before ``clean_grants`` teardown.

    Agents created through ``POST /agents`` are stamped ``created_by`` with the
    caller, not the seed marker, so ``clean_grants`` would not remove them and
    the owner FK would block the seeded users' deletion.
    """
    for user_id in (OWNER, OTHER, ADMIN):
        await seeds.seed_user(integration_context, user_id)
    yield
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.owner_id.in_([OWNER, OTHER, ADMIN])))
        await session.commit()


@pytest.fixture()
async def self_register(
    integration_context: Context, users: None
) -> AsyncGenerator[Callable[[str], Awaitable[str]], None]:
    """Self-register a pending agent through the real DCR service; delete it after."""
    created: list[str] = []

    async def _register(scope: str) -> str:
        result = await RegistrationService(integration_context).register(
            "self-registered", _DCR_JWKS, scope=scope
        )
        created.append(result.client_id)
        return result.client_id

    yield _register
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id.in_(created)))
        await session.commit()


def _writer(sub: str) -> Identity:
    return Identity(sub=sub, email=f"{sub}@authz.test", permissions=list(_WRITER_PERMS))


def _admin() -> Identity:
    return Identity(sub=ADMIN, email=f"{ADMIN}@authz.test", permissions=["org:admin"])


@asynccontextmanager
async def _client(ctx: Context, identity: Identity) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.dependency_overrides[resolve_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _create_owned_agent(ctx: Context) -> str:
    async with _client(ctx, _writer(OWNER)) as client:
        resp = await client.post("/agents", json={"name": "owned-agent"})
    assert resp.status_code == 201, resp.text
    agent_id: str = resp.json()["id"]
    return agent_id


async def _scopes(ctx: Context, agent_id: str) -> set[str]:
    async with ctx.admin_db.session() as session:
        grants = await ActorScopeGrantRepository.list_for_actor(session, agent_id)
    return {g.scope for g in grants}


async def _agent(ctx: Context, agent_id: str) -> Agent:
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
    assert agent is not None
    return agent


async def _audit_scopes(ctx: Context, agent_id: str, action: AuditAction) -> object:
    async with ctx.admin_db.session() as session:
        entries = await AuditRepository.list_by_target(session, AuditTargetType.AGENT, agent_id)
    matching = [e for e in entries if e.action == action]
    assert len(matching) == 1
    assert matching[0].after is not None
    return matching[0].after.get("scopes")


async def _register_audit_scopes(ctx: Context, agent_id: str) -> object:
    return await _audit_scopes(ctx, agent_id, AuditAction.REGISTER)


# ---------------------------------------------------------------------------
# Scope ceiling — POST /agents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["org:admin", "agents:write", "users:write"])
async def test_create_rejects_scope_above_caller_ceiling(
    integration_context: Context, users: None, scope: str
) -> None:
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post("/agents", json={"name": "escalate", "scopes": [scope]})
    assert resp.status_code == 403
    assert resp.json()["type"] == "scope_not_grantable"


async def test_create_rejects_unknown_scope(integration_context: Context, users: None) -> None:
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post("/agents", json={"name": "bogus", "scopes": ["made:up"]})
    assert resp.status_code == 422
    assert resp.json()["type"] == "unknown_scope"


async def test_create_without_scopes_grants_defaults_and_audits_them(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    assert await _scopes(integration_context, agent_id) == set(DEFAULT_AGENT_SCOPES)
    assert await _register_audit_scopes(integration_context, agent_id) == list(DEFAULT_AGENT_SCOPES)


async def test_create_with_held_or_baseline_scopes_succeeds(
    integration_context: Context, users: None
) -> None:
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.post(
            "/agents", json={"name": "narrow", "scopes": ["agents:read", "capabilities:read"]}
        )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    assert await _scopes(integration_context, agent_id) == {"agents:read", "capabilities:read"}
    assert await _register_audit_scopes(integration_context, agent_id) == [
        "agents:read",
        "capabilities:read",
    ]


async def test_admin_create_may_grant_org_admin(integration_context: Context, users: None) -> None:
    async with _client(integration_context, _admin()) as client:
        resp = await client.post("/agents", json={"name": "admin-agent", "scopes": ["org:admin"]})
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    assert await _scopes(integration_context, agent_id) == {"org:admin"}
    assert await _register_audit_scopes(integration_context, agent_id) == ["org:admin"]


# ---------------------------------------------------------------------------
# Scope ceiling + owner scoping — PUT /agents/{id}/scopes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["org:admin", "agents:write", "users:write"])
async def test_replace_scopes_rejects_scope_above_caller_ceiling(
    integration_context: Context, users: None, scope: str
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(f"/agents/{agent_id}/scopes", json={"scopes": [scope]})
    assert resp.status_code == 403
    assert resp.json()["type"] == "scope_not_grantable"
    assert await _scopes(integration_context, agent_id) == set(DEFAULT_AGENT_SCOPES)


async def test_replace_scopes_rejects_unknown_scope(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(f"/agents/{agent_id}/scopes", json={"scopes": ["made:up"]})
    assert resp.status_code == 422
    assert resp.json()["type"] == "unknown_scope"


async def test_owner_can_narrow_scopes(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/scopes", json={"scopes": ["capabilities:read"]}
        )
    assert resp.status_code == 200, resp.text
    assert await _scopes(integration_context, agent_id) == {"capabilities:read"}


async def test_owner_may_keep_scope_an_admin_granted(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _admin()) as client:
        resp = await client.put(
            f"/agents/{agent_id}/scopes", json={"scopes": ["org:admin", "capabilities:read"]}
        )
    assert resp.status_code == 200, resp.text
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.put(f"/agents/{agent_id}/scopes", json={"scopes": ["org:admin"]})
    assert resp.status_code == 200, resp.text
    assert await _scopes(integration_context, agent_id) == {"org:admin"}


async def test_non_owner_cannot_replace_scopes(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.put(
            f"/agents/{agent_id}/scopes", json={"scopes": ["capabilities:read"]}
        )
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"
    assert await _scopes(integration_context, agent_id) == set(DEFAULT_AGENT_SCOPES)


# ---------------------------------------------------------------------------
# Owner scoping — PATCH / disable / enable / archive
# ---------------------------------------------------------------------------


async def test_non_owner_cannot_patch_or_take_over(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OTHER)) as client:
        for body in ({"name": "renamed"}, {"owner_id": OTHER}):
            resp = await client.patch(f"/agents/{agent_id}", json=body)
            assert resp.status_code == 404, body
            assert resp.json()["type"] == "actor_not_found"
    agent = await _agent(integration_context, agent_id)
    assert agent.owner_id == OWNER
    assert agent.name == "owned-agent"


async def test_owner_transfer_is_admin_only(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        resp = await client.patch(f"/agents/{agent_id}", json={"owner_id": OTHER})
        assert resp.status_code == 403
        assert resp.json()["type"] == "owner_transfer_forbidden"
        assert (await _agent(integration_context, agent_id)).owner_id == OWNER

        # Re-sending the current owner is not a transfer.
        resp = await client.patch(
            f"/agents/{agent_id}", json={"name": "renamed", "owner_id": OWNER}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "renamed"

    async with _client(integration_context, _admin()) as client:
        resp = await client.patch(f"/agents/{agent_id}", json={"owner_id": OTHER})
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_id"] == OTHER


async def test_non_owner_cannot_disable_enable_or_archive(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OTHER)) as other:
        assert (await other.post(f"/agents/{agent_id}:disable")).status_code == 404
        assert (await other.delete(f"/agents/{agent_id}")).status_code == 404
        async with _client(integration_context, _writer(OWNER)) as owner:
            assert (await owner.post(f"/agents/{agent_id}:disable")).status_code == 204
        resp = await other.post(f"/agents/{agent_id}:enable")
        assert resp.status_code == 404
        assert resp.json()["type"] == "actor_not_found"

    assert (await _agent(integration_context, agent_id)).status == ActorStatus.DISABLED
    assert await _scopes(integration_context, agent_id) == set(DEFAULT_AGENT_SCOPES)


async def test_owner_can_disable_enable_and_archive(
    integration_context: Context, users: None
) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _writer(OWNER)) as client:
        assert (await client.post(f"/agents/{agent_id}:disable")).status_code == 204
        assert (await client.post(f"/agents/{agent_id}:enable")).status_code == 204
        assert (await client.delete(f"/agents/{agent_id}")).status_code == 204
    assert (await _agent(integration_context, agent_id)).status == ActorStatus.ARCHIVED


async def test_admin_can_mutate_any_agent(integration_context: Context, users: None) -> None:
    agent_id = await _create_owned_agent(integration_context)
    async with _client(integration_context, _admin()) as client:
        resp = await client.put(f"/agents/{agent_id}/scopes", json={"scopes": ["apis:read"]})
        assert resp.status_code == 200, resp.text
        assert (await client.patch(f"/agents/{agent_id}", json={"name": "x"})).status_code == 200
        assert (await client.post(f"/agents/{agent_id}:disable")).status_code == 204
        assert (await client.post(f"/agents/{agent_id}:enable")).status_code == 204
        assert (await client.delete(f"/agents/{agent_id}")).status_code == 204


async def test_unknown_agent_is_404_for_owner_scoped_verbs(
    integration_context: Context, users: None
) -> None:
    missing = "agnt_does_not_exist"
    async with _client(integration_context, _writer(OWNER)) as client:
        responses = [
            await client.put(f"/agents/{missing}/scopes", json={"scopes": []}),
            await client.patch(f"/agents/{missing}", json={"name": "x"}),
            await client.post(f"/agents/{missing}:disable"),
            await client.post(f"/agents/{missing}:enable"),
            await client.delete(f"/agents/{missing}"),
        ]
    for resp in responses:
        assert resp.status_code == 404
        assert resp.json()["type"] == "actor_not_found"


# ---------------------------------------------------------------------------
# Registration decisions stay approver-wide
# ---------------------------------------------------------------------------


async def test_any_approver_can_approve_pending_agent(
    integration_context: Context, users: None
) -> None:
    agent_id = await seeds.seed_agent(
        integration_context, owner_id=OWNER, scopes=[], status=ActorStatus.PENDING
    )
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


async def test_any_approver_can_deny_pending_agent(
    integration_context: Context, users: None
) -> None:
    agent_id = await seeds.seed_agent(
        integration_context, owner_id=OWNER, scopes=[], status=ActorStatus.PENDING
    )
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:deny", json={"reason": "no"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"


@pytest.mark.parametrize("requested", ["org:admin", "agents:write", "users:write"])
async def test_non_admin_approver_cannot_activate_requested_scope_above_ceiling(
    integration_context: Context,
    self_register: Callable[[str], Awaitable[str]],
    requested: str,
) -> None:
    agent_id = await self_register(f"capabilities:read {requested}")
    assert await _register_audit_scopes(integration_context, agent_id) == [
        "capabilities:read",
        requested,
    ]
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 403
    assert resp.json()["type"] == "scope_not_grantable"
    assert (await _agent(integration_context, agent_id)).status == ActorStatus.PENDING

    async with _client(integration_context, _admin()) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert await _scopes(integration_context, agent_id) == {"capabilities:read", requested}


async def test_non_admin_approver_can_activate_requested_default_scopes(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    # Unknown requested strings grant nothing and do not block the decision.
    agent_id = await self_register("capabilities:execute agents:read not-a:scope")
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    expected = ["agents:read", "capabilities:execute", "not-a:scope"]
    assert await _scopes(integration_context, agent_id) == set(expected)
    approve_scopes = await _audit_scopes(integration_context, agent_id, AuditAction.APPROVE)
    assert isinstance(approve_scopes, list)
    assert sorted(approve_scopes) == expected


async def test_approve_without_requested_scopes_grants_and_audits_defaults(
    integration_context: Context, self_register: Callable[[str], Awaitable[str]]
) -> None:
    agent_id = await self_register("")
    async with _client(integration_context, _writer(OTHER)) as client:
        resp = await client.post(f"/agents/{agent_id}:approve")
    assert resp.status_code == 200, resp.text
    assert await _scopes(integration_context, agent_id) == set(DEFAULT_AGENT_SCOPES)
    assert await _audit_scopes(integration_context, agent_id, AuditAction.APPROVE) == list(
        DEFAULT_AGENT_SCOPES
    )
