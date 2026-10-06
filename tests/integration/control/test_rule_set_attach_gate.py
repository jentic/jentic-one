"""Integration tests for who may attach (and edit) a shared permission rule set.

A set is attachable by its creator or ``org:admin``; a curated set (created by
an ``org:admin``) by any caller who may write the binding's rules. Curated sets
are editable only by ``org:admin``. Runs against real control and admin
databases on both dialects.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from jentic_one.admin.repos.agent_credential_binding_repo import AgentCredentialBindingRepository
from jentic_one.control.repos import PermissionRuleSetRepository
from jentic_one.control.services.credentials.errors import (
    RuleSetAccessDeniedError,
    RuleSetAttachDeniedError,
)
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.rule_set_curation import RuleSetCurationService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType

pytestmark = pytest.mark.integration

_WRITE = ["credentials:read", "credentials:write"]
_ALICE = Identity(sub="usr_rsgate_alice", email="alice@test.local", permissions=_WRITE)
_BOB = Identity(sub="usr_rsgate_bob", email="bob@test.local", permissions=_WRITE)
_ADMIN = Identity(sub="usr_rsgate_admin", email="admin@test.local", permissions=["org:admin"])
_USERS = (_ALICE, _BOB, _ADMIN)
_RULES: list[dict[str, object]] = [{"effect": "allow", "methods": ["GET"]}]


@dataclass(frozen=True)
class _Binding:
    credential_id: str
    agent_id: str
    owner: Identity


@pytest.fixture()
def svc(integration_context: Context) -> CredentialService:
    return CredentialService(integration_context)


@pytest.fixture()
async def cleanup(integration_context: Context) -> AsyncGenerator[None, None]:
    await _cleanup(integration_context)
    yield
    await _cleanup(integration_context)


async def _cleanup(ctx: Context) -> None:
    subs = {"a": _ALICE.sub, "b": _BOB.sub, "c": _ADMIN.sub}
    async with ctx.admin_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE created_by IN (:a, :b, :c)"), subs
        )
        await session.execute(text("DELETE FROM agents WHERE registered_by IN (:a, :b, :c)"), subs)
        await session.execute(
            text("DELETE FROM user_permission_grants WHERE user_id IN (:a, :b, :c)"), subs
        )
        await session.execute(text("DELETE FROM users WHERE id IN (:a, :b, :c)"), subs)
        await session.commit()
    async with ctx.control_db.session() as session:
        await session.execute(
            text(
                "DELETE FROM permission_rule_sets "
                "WHERE created_by IN (:a, :b, :c) OR name LIKE 'curation-%'"
            ),
            subs,
        )
        await session.execute(
            text("DELETE FROM credentials WHERE created_by IN (:a, :b, :c)"), subs
        )
        await session.commit()


async def _binding(ctx: Context, svc: CredentialService, owner: Identity, tag: str) -> _Binding:
    """A credential owned by ``owner`` bound to an agent ``owner`` registered."""
    created = await svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name=f"rsgate-{tag}",
            api=APIReference(vendor="rsgate-vendor", name="rsgate-api", version="v1"),
            token="sk-rsgate-token-value-123",
        ),
        identity=owner,
    )
    agent_id = f"agnt_rsgate_{tag}"
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status, created_by) "
                "VALUES (:id, :name, :owner, 'approved', :owner)"
            ),
            {"id": agent_id, "name": f"rsgate-{tag}", "owner": owner.sub},
        )
        await AgentCredentialBindingRepository.bind(
            session, agent_id=agent_id, credential_id=created.credential_id, created_by=owner.sub
        )
    return _Binding(created.credential_id, agent_id, owner)


async def _attached(svc: CredentialService, b: _Binding) -> str | None:
    rows, _, _ = await svc.list_agents(b.credential_id, identity=b.owner)
    return next(r.rule_set_id for r in rows if r.agent_id == b.agent_id)


async def _attach(svc: CredentialService, b: _Binding, rule_set_id: str, who: Identity) -> None:
    await svc.attach_binding_rule_set(b.credential_id, b.agent_id, rule_set_id, identity=who)


async def test_creator_attaches_own_set(
    integration_context: Context, svc: CredentialService, cleanup: None
) -> None:
    b = await _binding(integration_context, svc, _ALICE, "own")
    rule_set, _ = await svc.create_rule_set(
        name="alice-set", description=None, rules=_RULES, identity=_ALICE
    )
    assert rule_set.curated is False

    await _attach(svc, b, rule_set.id, _ALICE)

    assert await _attached(svc, b) == rule_set.id


async def test_other_user_cannot_attach_a_non_admin_users_set(
    integration_context: Context, svc: CredentialService, cleanup: None
) -> None:
    b = await _binding(integration_context, svc, _BOB, "other")
    alice_set, _ = await svc.create_rule_set(
        name="alice-private", description=None, rules=_RULES, identity=_ALICE
    )

    with pytest.raises(RuleSetAttachDeniedError):
        await _attach(svc, b, alice_set.id, _BOB)

    assert await _attached(svc, b) is None


async def test_any_writer_can_attach_an_admin_created_set(
    integration_context: Context, svc: CredentialService, cleanup: None
) -> None:
    b = await _binding(integration_context, svc, _BOB, "curated")
    admin_set, _ = await svc.create_rule_set(
        name="admin-shared", description=None, rules=_RULES, identity=_ADMIN
    )
    assert admin_set.curated is True

    await _attach(svc, b, admin_set.id, _BOB)

    assert await _attached(svc, b) == admin_set.id


async def test_admin_can_attach_any_set(
    integration_context: Context, svc: CredentialService, cleanup: None
) -> None:
    b = await _binding(integration_context, svc, _BOB, "admin")
    alice_set, _ = await svc.create_rule_set(
        name="alice-for-admin", description=None, rules=_RULES, identity=_ALICE
    )

    await _attach(svc, b, alice_set.id, _ADMIN)

    assert await _attached(svc, b) == alice_set.id


async def test_existing_attachment_is_unaffected(
    integration_context: Context, svc: CredentialService, cleanup: None
) -> None:
    """A binding already on another user's set keeps it, and re-attaching it is a no-op."""
    b = await _binding(integration_context, svc, _BOB, "existing")
    alice_set, _ = await svc.create_rule_set(
        name="alice-existing", description=None, rules=_RULES, identity=_ALICE
    )
    # Attached before the gate existed (an admin attach stands in for that here).
    await _attach(svc, b, alice_set.id, _ADMIN)

    await _attach(svc, b, alice_set.id, _BOB)
    result = await svc.test_agent_permissions(
        b.credential_id,
        b.agent_id,
        method="GET",
        path="/anything",
        operation_id=None,
        identity=_BOB,
    )

    assert await _attached(svc, b) == alice_set.id
    assert result.allowed is True
    # Detaching stays open to the binding's owner.
    await svc.detach_binding_rule_set(b.credential_id, b.agent_id, identity=_BOB)
    assert await _attached(svc, b) is None


async def test_curated_set_is_editable_only_by_admin(
    integration_context: Context, svc: CredentialService, cleanup: None
) -> None:
    """The admin who created a curated set loses edit rights with ``org:admin``."""
    admin_set, _ = await svc.create_rule_set(
        name="admin-edit", description=None, rules=_RULES, identity=_ADMIN
    )
    demoted = Identity(sub=_ADMIN.sub, email=_ADMIN.email, permissions=_WRITE)

    with pytest.raises(RuleSetAccessDeniedError):
        await svc.update_rule_set(admin_set.id, identity=demoted, description="changed")
    with pytest.raises(RuleSetAccessDeniedError):
        await svc.replace_rule_set_rules(admin_set.id, [], identity=_BOB)

    updated = await svc.update_rule_set(admin_set.id, identity=_ADMIN, description="changed")
    assert updated.description == "changed"


async def test_curation_step_marks_admin_and_system_created_sets(
    integration_context: Context, cleanup: None
) -> None:
    """The upgrade step marks sets whose creator holds org:admin or is a system actor."""
    async with integration_context.admin_db.transaction() as session:
        for user in _USERS:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name) "
                    "VALUES (:id, :email, 'Rule', 'Set')"
                ),
                {"id": user.sub, "email": user.email},
            )
        await session.execute(
            text(
                "INSERT INTO user_permission_grants (id, user_id, permission) "
                "VALUES ('perm_rsgate_admin', :uid, 'org:admin')"
            ),
            {"uid": _ADMIN.sub},
        )
    creators = {
        "by-admin": _ADMIN.sub,
        "by-alice": _ALICE.sub,
        "by-system": "system:theme5-flattening",
        "by-nobody": None,
    }
    async with integration_context.control_db.transaction() as session:
        ids = {
            name: (
                await PermissionRuleSetRepository.create(
                    session,
                    name=f"curation-{name}",
                    description=None,
                    created_by=creator,  # type: ignore[arg-type]
                )
            ).id
            for name, creator in creators.items()
        }

    first = await RuleSetCurationService(integration_context).mark_existing()
    second = await RuleSetCurationService(integration_context).mark_existing()

    async with integration_context.control_db.session() as session:
        curated = {
            name: (await PermissionRuleSetRepository.get_by_id(session, sid)).curated  # type: ignore[union-attr]
            for name, sid in ids.items()
        }
    assert curated == {"by-admin": True, "by-alice": False, "by-system": True, "by-nobody": False}
    assert (first.marked, first.admin_creators) == (2, 1)
    assert second.marked == 0
