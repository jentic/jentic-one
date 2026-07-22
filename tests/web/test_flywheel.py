"""End-to-end "flywheel" integration test for the admin-console flow.

Exercises the whole loop against the REAL combined control-plane app + a real
database, spanning three surfaces (admin `/users`, auth `/agents`, control
`/toolkits`):

  1. an org admin exists;
  2. the admin creates multiple users with different permission sets;
  3. the admin and a write-capable user create agents and toolkits;
  4. reads are verified BY ROLE — who can list what, who is 403'd, and that the
     owner-scoping filter shows admins everything but non-admins only their own.

This is the durable version of `scripts/flywheel_manual.py`. Actors are switched
by minting per-actor JWTs with ``issue_jwt`` (the real ``resolve_identity`` path),
mirroring ``tests/web/admin/test_users.py``. Deterministic across repeat runs:
every entity uses a unique suffix and is created through the real API, and the
seeded users/permissions are cleaned up on teardown.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from jentic_one.shared.web.app_factory import create_combined_app
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

_SURFACES = ["registry", "admin", "control", "auth"]


def _suffix() -> str:
    return uuid.uuid4().hex[:12]


def _build_combined_app(ctx: Context) -> FastAPI:
    """Build the real combined app (all surfaces) with lifespan disabled.

    The combined factory installs the admin surface's ``verify_token``, so real
    JWTs are resolved exactly as in production.
    """
    app = create_combined_app(ctx, _SURFACES)
    app.router.lifespan_context = noop_lifespan
    return app


def _token(ctx: Context, *, sub: str, email: str, permissions: list[str]) -> str:
    """Mint a signed JWT for an actor with the given effective permissions."""
    auth = ctx.config.admin.auth
    claims = {
        "sub": sub,
        "email": email,
        "actor_type": "user",
        "permissions": permissions,
        "must_change_password": False,
    }
    return issue_jwt(claims, auth.jwt_secret.get_secret_value(), auth.jwt_ttl_seconds)


class _Actor:
    def __init__(self, user_id: str, email: str, token: str) -> None:
        self.id = user_id
        self.email = email
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def actors(web_context: Context) -> AsyncGenerator[dict[str, _Actor], None]:
    """Seed three real users with distinct permission sets and mint their tokens.

    - admin:   org:admin (sees + does everything)
    - manager: agents/toolkits read+write (can create; owner-scoped reads)
    - reader:  agents/toolkits read only (can list, cannot create; no users:read)

    Users are created directly via repositories (fast + deterministic) rather
    than the invite→redeem round-trip; the API-level create flow is covered
    separately below.
    """
    ctx = web_context
    sfx = _suffix()
    specs = {
        "admin": (f"fw-admin-{sfx}@test.local", {"org:admin"}),
        "manager": (
            f"fw-mgr-{sfx}@test.local",
            {"agents:read", "agents:write", "toolkits:read", "toolkits:write"},
        ),
        "reader": (f"fw-reader-{sfx}@test.local", {"agents:read", "toolkits:read"}),
    }
    created: dict[str, _Actor] = {}
    ids: list[str] = []
    async with ctx.admin_db.session() as session:
        for role, (email, perms) in specs.items():
            user = await UserRepository.create(
                session,
                email=email,
                first_name=role.capitalize(),
                last_name="Flywheel",
                invite_state=InviteState.REDEEMED,
                created_by="usr_test",
            )
            await UserSecretRepository.create(
                session,
                user_id=user.id,
                password_hash=hash_password("S3curePassw0rd!"),
                created_by="usr_test",
            )
            await UserPermissionGrantRepository.set_permissions(
                session, user.id, permissions=perms, granted_by=None, created_by="usr_test"
            )
            created[role] = _Actor(
                user.id, email, _token(ctx, sub=user.id, email=email, permissions=sorted(perms))
            )
            ids.append(user.id)
        await session.commit()

    yield created

    # Teardown order matters: agents/service-accounts FK-reference the owning
    # user (agents.owner_id) and toolkits carry created_by, so remove everything
    # these actors created before deleting the users themselves.
    async with ctx.control_db.session() as session:
        await session.execute(delete(Toolkit).where(Toolkit.created_by.in_(ids)))
        await session.commit()
    async with ctx.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.owner_id.in_(ids)))
        await session.execute(delete(ServiceAccount).where(ServiceAccount.owner_id.in_(ids)))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id.in_(ids))
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id.in_(ids)))
        await session.execute(delete(User).where(User.id.in_(ids)))
        await session.commit()


@pytest.fixture()
def client(web_context: Context) -> Iterator[TestClient]:
    app = _build_combined_app(web_context)
    with TestClient(app) as tc:
        yield tc


def _list_names(client: TestClient, path: str, actor: _Actor) -> list[str]:
    resp = client.get(path, headers=actor.headers)
    assert resp.status_code == 200, resp.text
    return [row["name"] for row in resp.json().get("data", [])]


def test_admin_can_create_users_via_api(
    client: TestClient, actors: dict[str, _Actor]
) -> None:
    """Admin invites a user through the real API and gets an invite token."""
    admin = actors["admin"]
    email = f"fw-invitee-{_suffix()}@test.local"
    resp = client.post(
        "/users",
        headers=admin.headers,
        json={"email": email, "first_name": "New", "last_name": "User", "permissions": []},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invite_token"]
    assert body["user"]["email"] == email
    # The invitee can complete setup by redeeming (proves the link works).
    redeem = client.post(
        "/users:redeem-invite",
        json={"invite_token": body["invite_token"], "password": "S3curePassw0rd!"},
    )
    assert redeem.status_code == 200, redeem.text
    assert redeem.json()["access_token"]
    # Cleanup the invitee.
    client.delete(f"/users/{body['user']['id']}", headers=admin.headers)


def test_non_admin_cannot_create_users(
    client: TestClient, actors: dict[str, _Actor]
) -> None:
    resp = client.post(
        "/users",
        headers=actors["manager"].headers,
        json={"email": f"x-{_suffix()}@test.local", "first_name": "X", "last_name": "Y"},
    )
    assert resp.status_code == 403, resp.text


def test_write_capable_actors_create_agents_reader_denied(
    client: TestClient, actors: dict[str, _Actor]
) -> None:
    admin, manager, reader = actors["admin"], actors["manager"], actors["reader"]
    admin_agent = f"fw-admin-agent-{_suffix()}"
    mgr_agent = f"fw-mgr-agent-{_suffix()}"

    r = client.post("/agents", headers=admin.headers, json={"name": admin_agent})
    assert r.status_code == 201, r.text
    r = client.post("/agents", headers=manager.headers, json={"name": mgr_agent})
    assert r.status_code == 201, r.text
    # Reader lacks agents:write.
    r = client.post(
        "/agents", headers=reader.headers, json={"name": f"fw-rdr-agent-{_suffix()}"}
    )
    assert r.status_code == 403, r.text


def test_write_capable_actors_create_toolkits_reader_denied(
    client: TestClient, actors: dict[str, _Actor]
) -> None:
    admin, manager, reader = actors["admin"], actors["manager"], actors["reader"]
    r = client.post(
        "/toolkits", headers=admin.headers, json={"name": f"fw-admin-tk-{_suffix()}"}
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/toolkits", headers=manager.headers, json={"name": f"fw-mgr-tk-{_suffix()}"}
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/toolkits", headers=reader.headers, json={"name": f"fw-rdr-tk-{_suffix()}"}
    )
    assert r.status_code == 403, r.text


def test_reads_by_role_and_owner_scoping(
    client: TestClient, actors: dict[str, _Actor]
) -> None:
    """Everyone with a read scope can list; owner-scoping means the admin
    sees agents others created but a non-admin sees only their own."""
    admin, manager, reader = actors["admin"], actors["manager"], actors["reader"]
    admin_agent = f"fw-admin-agent-{_suffix()}"
    mgr_agent = f"fw-mgr-agent-{_suffix()}"
    assert (
        client.post("/agents", headers=admin.headers, json={"name": admin_agent}).status_code
        == 201
    )
    assert (
        client.post("/agents", headers=manager.headers, json={"name": mgr_agent}).status_code
        == 201
    )

    admin_view = _list_names(client, "/agents", admin)
    manager_view = _list_names(client, "/agents", manager)
    _list_names(client, "/agents", reader)  # reader can read (200), sees none of these

    # Admin sees both agents; manager sees its own but NOT the admin's.
    assert admin_agent in admin_view
    assert mgr_agent in admin_view
    assert mgr_agent in manager_view
    assert admin_agent not in manager_view


def test_admin_only_user_listing(client: TestClient, actors: dict[str, _Actor]) -> None:
    admin, reader = actors["admin"], actors["reader"]
    assert client.get("/users", headers=admin.headers).status_code == 200
    # Reader has no users:read scope.
    assert client.get("/users", headers=reader.headers).status_code == 403
