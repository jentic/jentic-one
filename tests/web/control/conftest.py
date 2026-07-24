"""Control web test fixtures — real services, real database, overridden identity."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from jentic_one.admin.core.permissions import compute_effective
from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.deps import resolve_identity
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration


def _effective(*permissions: str) -> list[str]:
    """Expand an assigned permission set to its effective closure.

    Mirrors production identity resolution (``PermissionService.get_effective_*``
    returns the implication-expanded set), which the ``resolve_identity`` override
    bypasses. Without this, an identity granted only ``toolkits:write`` would fail
    the route-level ``toolkits:read`` intersection check that the dependency
    performs verbatim against ``identity.permissions``.
    """
    return sorted(compute_effective(set(permissions)))


FILER_SUB = "agnt_webtest_filer"
OWNER_SUB = "usr_webtest_owner"
REVIEWER_SUB = "usr_webtest_reviewer"
UNRELATED_SUB = "usr_webtest_unrelated"
ADMIN_SUB = "usr_webtest_admin"

FILER_IDENTITY = Identity(
    sub=FILER_SUB,
    email="filer@test.local",
    permissions=[],
    actor_type=ActorType.AGENT,
    parent_actor_id=OWNER_SUB,
)

OWNER_IDENTITY = Identity(
    sub=OWNER_SUB,
    email="owner@test.local",
    permissions=["agents:write"],
)

UNRELATED_IDENTITY = Identity(
    sub=UNRELATED_SUB,
    email="unrelated@test.local",
    permissions=["agents:write"],
)

ADMIN_IDENTITY = Identity(
    sub=ADMIN_SUB,
    email="admin@test.local",
    permissions=["org:admin"],
)


def _build_app(ctx: Context, identity: Identity) -> FastAPI:
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    return app


@pytest.fixture()
async def seed_binding(web_context: Context) -> AsyncGenerator[None, None]:
    """Seed an agent + binding so prerequisite checks pass.

    Also seeds the control-side toolkit + credential referenced by the
    ``credential:bind`` items the tests file, so that approving such an item
    records a real binding (the FKs on ``toolkit_credential_bindings`` require
    both rows to exist).
    """
    async with web_context.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status) "
                "VALUES (:id, :name, :registered_by, 'active') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": FILER_SUB, "name": "test-filer-agent", "registered_by": OWNER_SUB},
        )
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id) "
                "VALUES (:id, :agent_id, :toolkit_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": "atb_webtest_binding", "agent_id": FILER_SUB, "toolkit_id": "tk_target"},
        )
        await session.commit()
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, created_by) "
                "VALUES (:id, :name, :created_by) ON CONFLICT DO NOTHING"
            ),
            {"id": "tk_target", "name": "tk-target-webtest", "created_by": OWNER_SUB},
        )
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES (:id, 'token_value', :name, :vendor, :created_by) ON CONFLICT DO NOTHING"
            ),
            {
                "id": "cred_001",
                "name": "cred-webtest",
                "vendor": "webtest.local",
                "created_by": OWNER_SUB,
            },
        )
        await session.commit()
    yield
    async with web_context.admin_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_toolkit_bindings WHERE id = :id"),
            {"id": "atb_webtest_binding"},
        )
        await session.execute(
            text("DELETE FROM agents WHERE id = :id"),
            {"id": FILER_SUB},
        )
        await session.commit()
    async with web_context.control_db.session() as session:
        await session.execute(
            text("DELETE FROM toolkit_credential_bindings WHERE toolkit_id = :id"),
            {"id": "tk_target"},
        )
        await session.execute(text("DELETE FROM credentials WHERE id = :id"), {"id": "cred_001"})
        await session.execute(text("DELETE FROM toolkits WHERE id = :id"), {"id": "tk_target"})
        await session.commit()


@pytest.fixture()
async def clean_access_requests(web_context: Context) -> AsyncGenerator[None, None]:
    async with web_context.control_db.session() as session:
        await session.execute(delete(AccessRequestItem))
        await session.execute(delete(AccessRequest))
        await session.commit()
    yield
    async with web_context.control_db.session() as session:
        await session.execute(delete(AccessRequestItem))
        await session.execute(delete(AccessRequest))
        await session.commit()


# A bound but orphaned agent (issues #665/#682): it owns nothing (parent_actor_id
# None, like the jentic-cli-default bootstrap agent) but is bound to tk_target via
# the seed_binding fixture. It carries the default agent owner-read scopes so it
# passes the route gate; visibility must then come purely from the binding.
BOUND_ORPHAN_IDENTITY = Identity(
    sub=FILER_SUB,
    email="orphan@test.local",
    permissions=["owner:toolkits:read", "owner:credentials:read"],
    actor_type=ActorType.AGENT,
    parent_actor_id=None,
)


@pytest.fixture()
def bound_orphan_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    """TestClient as a bound-but-orphaned agent (owns nothing, bound to tk_target)."""
    app = _build_app(web_context, BOUND_ORPHAN_IDENTITY)
    with TestClient(app) as tc:
        yield tc


# A bound orphan that ALSO holds toolkits:write (issue #682, write path): it owns
# nothing but is bound to tk_target, so a write to that toolkit must succeed —
# not 404 as owner-only scoping produced before the fix.
BOUND_ORPHAN_WRITER_IDENTITY = Identity(
    sub=FILER_SUB,
    email="orphan-writer@test.local",
    permissions=["toolkits:write", "owner:toolkits:read", "owner:credentials:read"],
    actor_type=ActorType.AGENT,
    parent_actor_id=None,
)

# A writer that neither owns nor is bound to tk_target and is not org:admin. A
# write to tk_target (which exists, owned by OWNER_SUB) must be 403 — naming the
# real requirement — not a misleading 404 (issue #682).
UNBOUND_WRITER_IDENTITY = Identity(
    sub="usr_webtest_unbound_writer",
    email="unbound-writer@test.local",
    permissions=["toolkits:write"],
)


@pytest.fixture()
def bound_orphan_writer_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    """TestClient as a bound-but-orphaned agent that holds toolkits:write."""
    app = _build_app(web_context, BOUND_ORPHAN_WRITER_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def unbound_writer_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    """TestClient holding toolkits:write but neither owning nor bound to tk_target."""
    app = _build_app(web_context, UNBOUND_WRITER_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def admin_writer_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    """TestClient as org:admin against the seed_binding toolkit (tk_target)."""
    app = _build_app(web_context, ADMIN_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def filer_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    app = _build_app(web_context, FILER_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def owner_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    app = _build_app(web_context, OWNER_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def unrelated_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    app = _build_app(web_context, UNRELATED_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def admin_client(
    web_context: Context, seed_binding: None, clean_access_requests: None
) -> Iterator[TestClient]:
    app = _build_app(web_context, ADMIN_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def unauthed_client(web_context: Context, clean_access_requests: None) -> Iterator[TestClient]:
    """TestClient with no identity override — exercises real auth path (no token → 401)."""
    app = create_app(web_context)
    app.router.lifespan_context = noop_lifespan
    with TestClient(app) as tc:
        yield tc


# --- Toolkit-focused clients (no access-request seeding) ---

TOOLKIT_OWNER_IDENTITY = Identity(
    sub="usr_webtest_tk_owner",
    email="tkowner@test.local",
    permissions=_effective("toolkits:write"),
)

TOOLKIT_ADMIN_IDENTITY = Identity(
    sub="usr_webtest_tk_admin",
    email="tkadmin@test.local",
    permissions=_effective("org:admin", "toolkits:write"),
)


@pytest.fixture()
async def clean_toolkits(web_context: Context) -> AsyncGenerator[None, None]:
    """Remove toolkits created by toolkit test identities."""
    yield
    async with web_context.control_db.session() as session:
        await session.execute(
            text("DELETE FROM toolkits WHERE created_by IN (:owner, :admin)"),
            {"owner": "usr_webtest_tk_owner", "admin": "usr_webtest_tk_admin"},
        )
        await session.commit()


@pytest.fixture()
def tk_owner_client(web_context: Context, clean_toolkits: None) -> Iterator[TestClient]:
    """TestClient as toolkit owner (has toolkits:write)."""
    app = _build_app(web_context, TOOLKIT_OWNER_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def tk_admin_client(web_context: Context, clean_toolkits: None) -> Iterator[TestClient]:
    """TestClient as org admin (has org:admin + toolkits:write)."""
    app = _build_app(web_context, TOOLKIT_ADMIN_IDENTITY)
    with TestClient(app) as tc:
        yield tc


# --- Permission-gating clients (least-privilege enforcement) ---

# A delegated agent minted the DEFAULT_AGENT_SCOPES owner-read scopes (never the
# bare credentials:read / toolkits:read). The route guard must admit it via the
# OR-listed owner scope so the control/scoping delegation filter can run.
DELEGATED_AGENT_IDENTITY = Identity(
    sub="agnt_webtest_delegated",
    email="delegated@test.local",
    permissions=["owner:credentials:read", "owner:toolkits:read"],
    actor_type=ActorType.AGENT,
    parent_actor_id="usr_webtest_delegated_owner",
)

# A caller holding a real scope, but not one that gates credentials/toolkits —
# proves the gate actually denies under-scoped callers (not just the happy path).
WRONG_SCOPE_IDENTITY = Identity(
    sub="usr_webtest_wrong_scope",
    email="wrongscope@test.local",
    permissions=["apis:read"],
)


@pytest.fixture()
def delegated_agent_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient as a delegated agent (owner:* read scopes, no bare read scope)."""
    app = _build_app(web_context, DELEGATED_AGENT_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def wrong_scope_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient holding only an unrelated scope (apis:read)."""
    app = _build_app(web_context, WRONG_SCOPE_IDENTITY)
    with TestClient(app) as tc:
        yield tc
