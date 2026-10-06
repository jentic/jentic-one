"""Integration tests for bound-agent visibility on a credential.

``org:admin`` and the credential's creator see every agent bound to the
credential. Any other caller who can see the credential (a bound agent, or a
user granted read access through the shared-read seam) sees only the bound
agents it can see itself: the agents it owns, itself when it is an agent, and
its owner's agents when it holds ``owner:agents:read``.

The rule covers ``CredentialService.list_agents`` and the per-binding rule
reads (``list_agent_permissions`` and ``test_agent_permissions``), where a
binding of an agent outside the caller's set raises the same
``AgentBindingNotFoundError`` as a binding that does not exist. Rule writes
stay owner-or-admin.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentCredentialBindingRepository
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.scoping import filters as control_filters
from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
)
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.scopes import OWNER_AGENTS_READ

pytestmark = pytest.mark.integration

_OWNER_SUB = "usr_cla_owner"
_SHAREE_SUB = "usr_cla_sharee"
_OTHER_SUB = "usr_cla_other"
_USERS = (_OWNER_SUB, _SHAREE_SUB, _OTHER_SUB)

_OWNER_AGENT = "agnt_cla_owner"
_SHAREE_AGENT = "agnt_cla_sharee"
_SHAREE_AGENT_2 = "agnt_cla_sharee2"
_OTHER_AGENT = "agnt_cla_other"
_AGENT_OWNERS = {
    _OWNER_AGENT: _OWNER_SUB,
    _SHAREE_AGENT: _SHAREE_SUB,
    _SHAREE_AGENT_2: _SHAREE_SUB,
    _OTHER_AGENT: _OTHER_SUB,
}
_ALL_AGENTS = set(_AGENT_OWNERS)
_BOUND_AT_BASE = datetime(2026, 1, 1, tzinfo=UTC)

_OWNER = Identity(sub=_OWNER_SUB, email="owner@test.local", permissions=["credentials:read"])
_ADMIN = Identity(sub="usr_cla_admin", email="admin@test.local", permissions=["org:admin"])
_SHAREE = Identity(sub=_SHAREE_SUB, email="sharee@test.local", permissions=["credentials:read"])


def _agent_identity(agent_id: str, *permissions: str) -> Identity:
    return Identity(
        sub=agent_id,
        email=f"{agent_id}@test.local",
        permissions=list(permissions),
        actor_type=ActorType.AGENT,
        parent_actor_id=_AGENT_OWNERS[agent_id],
    )


@pytest.fixture()
def share_with_sharee() -> Iterator[None]:
    """Register a shared-read provider that lets the sharee read every credential."""

    def _provider(identity: Identity, model: type) -> ColumnElement[bool] | None:
        if model is Credential and identity.sub == _SHAREE_SUB:
            return Credential.id.isnot(None)
        return None

    control_filters.register_access_filter_provider(_provider)
    yield
    control_filters._ACCESS_FILTER_PROVIDERS.remove(_provider)


async def _wipe(ctx: Context) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(_ALL_AGENTS))
        )
        await session.execute(delete(Agent).where(Agent.id.in_(_ALL_AGENTS)))
        await session.execute(delete(User).where(User.id.in_(_USERS)))
        await session.commit()
    async with ctx.control_db.session() as session:
        # Detail rows cascade from the credential row.
        await session.execute(delete(Credential).where(Credential.created_by == _OWNER_SUB))
        await session.commit()


@pytest.fixture()
async def bound_credential(
    integration_context: Context,
) -> AsyncGenerator[str, None]:
    """A credential created by the owner, bound to agents of three different users."""
    await _wipe(integration_context)
    created = await CredentialService(integration_context).create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="list-agents visibility",
            api=APIReference(vendor="test-vendor", name="test-api", version="v1"),
            token="sk-list-agents-visibility",
        ),
        identity=_OWNER,
    )
    async with integration_context.admin_db.transaction() as session:
        for sub in _USERS:
            session.add(User(id=sub, email=f"{sub}@test.local", first_name="T", last_name="U"))
        await session.flush()
        for agent_id, owner_id in _AGENT_OWNERS.items():
            session.add(
                Agent(id=agent_id, name=agent_id, owner_id=owner_id, registered_by=owner_id)
            )
        await session.flush()
        for offset, agent_id in enumerate(_AGENT_OWNERS):
            binding = await AgentCredentialBindingRepository.bind(
                session,
                agent_id=agent_id,
                credential_id=created.credential_id,
                created_by=_OWNER_SUB,
            )
            # Distinct, explicit bind times give the (bound_at, id) cursor a
            # stable order on both backends.
            binding.bound_at = _BOUND_AT_BASE + timedelta(seconds=offset)
    yield created.credential_id
    await _wipe(integration_context)


async def _listed(ctx: Context, credential_id: str, identity: Identity) -> set[str]:
    rows, has_more, _ = await CredentialService(ctx).list_agents(
        credential_id, identity=identity, limit=50
    )
    assert has_more is False
    return {row.agent_id for row in rows}


@pytest.mark.parametrize("identity", [_OWNER, _ADMIN], ids=["owner", "admin"])
async def test_owner_and_admin_see_every_bound_agent(
    integration_context: Context, bound_credential: str, identity: Identity
) -> None:
    assert await _listed(integration_context, bound_credential, identity) == _ALL_AGENTS


async def test_sharee_sees_only_own_agents(
    integration_context: Context, bound_credential: str, share_with_sharee: None
) -> None:
    listed = await _listed(integration_context, bound_credential, _SHAREE)
    assert listed == {_SHAREE_AGENT, _SHAREE_AGENT_2}


async def test_bound_agent_sees_only_itself(
    integration_context: Context, bound_credential: str
) -> None:
    identity = _agent_identity(_SHAREE_AGENT)
    assert await _listed(integration_context, bound_credential, identity) == {_SHAREE_AGENT}


async def test_bound_agent_with_owner_agents_read_sees_owner_agents(
    integration_context: Context, bound_credential: str
) -> None:
    identity = _agent_identity(_SHAREE_AGENT, OWNER_AGENTS_READ)
    listed = await _listed(integration_context, bound_credential, identity)
    assert listed == {_SHAREE_AGENT, _SHAREE_AGENT_2}


async def test_filtered_listing_paginates(
    integration_context: Context, bound_credential: str, share_with_sharee: None
) -> None:
    svc = CredentialService(integration_context)
    first, has_more, cursor = await svc.list_agents(bound_credential, identity=_SHAREE, limit=1)
    assert has_more is True
    assert cursor is not None
    second, has_more, _ = await svc.list_agents(
        bound_credential, identity=_SHAREE, limit=1, cursor=cursor
    )
    assert has_more is False
    assert {first[0].agent_id, second[0].agent_id} == {_SHAREE_AGENT, _SHAREE_AGENT_2}


async def test_caller_without_credential_visibility_gets_not_found(
    integration_context: Context, bound_credential: str
) -> None:
    with pytest.raises(CredentialNotFoundError):
        await CredentialService(integration_context).list_agents(bound_credential, identity=_SHAREE)


# --- Per-binding rule reads ---

_RULES: list[dict[str, object]] = [{"effect": "allow", "methods": ["GET"], "path": "/v1/.*"}]


async def _readable_bindings(ctx: Context, credential_id: str, identity: Identity) -> set[str]:
    """Agents whose binding rules ``identity`` can read, via both read paths.

    Asserts the list and the dry-run agree, so each test pins both reads.
    """
    svc = CredentialService(ctx)
    via_list: set[str] = set()
    via_test: set[str] = set()
    for agent_id in (*_ALL_AGENTS, "agnt_cla_never_bound"):
        try:
            rules = await svc.list_agent_permissions(credential_id, agent_id, identity=identity)
        except AgentBindingNotFoundError:
            pass
        else:
            assert len(rules) == len(_RULES)
            via_list.add(agent_id)
        try:
            result = await svc.test_agent_permissions(
                credential_id,
                agent_id,
                method="GET",
                path="/v1/things",
                operation_id=None,
                identity=identity,
            )
        except AgentBindingNotFoundError:
            pass
        else:
            assert result.allowed is True
            via_test.add(agent_id)
    assert via_list == via_test
    return via_list


@pytest.fixture()
async def ruled_credential(integration_context: Context, bound_credential: str) -> str:
    """``bound_credential`` with the same inline rule on every binding."""
    svc = CredentialService(integration_context)
    for agent_id in _ALL_AGENTS:
        await svc.replace_agent_permissions(bound_credential, agent_id, _RULES, identity=_OWNER)
    return bound_credential


@pytest.mark.parametrize("identity", [_OWNER, _ADMIN], ids=["owner", "admin"])
async def test_owner_and_admin_read_every_binding(
    integration_context: Context, ruled_credential: str, identity: Identity
) -> None:
    readable = await _readable_bindings(integration_context, ruled_credential, identity)
    assert readable == _ALL_AGENTS


async def test_sharee_reads_only_own_agents_bindings(
    integration_context: Context, ruled_credential: str, share_with_sharee: None
) -> None:
    readable = await _readable_bindings(integration_context, ruled_credential, _SHAREE)
    assert readable == {_SHAREE_AGENT, _SHAREE_AGENT_2}


async def test_bound_agent_reads_only_own_binding(
    integration_context: Context, ruled_credential: str
) -> None:
    identity = _agent_identity(_SHAREE_AGENT)
    readable = await _readable_bindings(integration_context, ruled_credential, identity)
    assert readable == {_SHAREE_AGENT}


async def test_bound_agent_with_owner_agents_read_reads_owner_agents_bindings(
    integration_context: Context, ruled_credential: str
) -> None:
    identity = _agent_identity(_SHAREE_AGENT, OWNER_AGENTS_READ)
    readable = await _readable_bindings(integration_context, ruled_credential, identity)
    assert readable == {_SHAREE_AGENT, _SHAREE_AGENT_2}


async def test_binding_read_without_credential_visibility_gets_credential_not_found(
    integration_context: Context, ruled_credential: str
) -> None:
    with pytest.raises(CredentialNotFoundError):
        await CredentialService(integration_context).list_agent_permissions(
            ruled_credential, _SHAREE_AGENT, identity=_SHAREE
        )


async def test_sharee_cannot_write_binding_rules(
    integration_context: Context, ruled_credential: str, share_with_sharee: None
) -> None:
    """Rule writes are owner-or-admin: read visibility, even of the sharee's own
    agent's binding, does not admit a write."""
    with pytest.raises(CredentialNotFoundError):
        await CredentialService(integration_context).replace_agent_permissions(
            ruled_credential, _SHAREE_AGENT, [], identity=_SHAREE
        )
