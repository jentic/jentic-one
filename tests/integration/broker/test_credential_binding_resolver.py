"""Integration tests for the cross-DB ``CredentialBindingResolver`` (theme-5 Phase 2).

Seeds the admin DB (direct agent→credential bindings) and the control DB
(credentials), then asserts ``derive_credentials`` returns the intersection for
an API identity: ``()`` (no overlap / suspended / inactive), one candidate
(with its ``rule_set_id``), and many candidates — plus the empty-set context
(``agent_bound_any`` / ``api_served`` / nearest-miss) the broker maps to the
right denial directive.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.shared.broker.protocols import CredentialDeriverProtocol
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_tables(
    admin_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Truncate the admin and control tables this module touches, before and after."""

    async def _truncate() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(Agent))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_agent(admin_db: DatabaseSession) -> str:
    agent = Agent(name="test-agent", registered_by="usr_test", status="approved")
    async with admin_db.session() as session:
        session.add(agent)
        await session.commit()
        return agent.id


async def _seed_credential(
    control_db: DatabaseSession,
    *,
    name: str,
    vendor: str,
    api_name: str | None,
    version: str | None,
    active: bool = True,
) -> str:
    credential = Credential(
        type="token_value",
        name=name,
        api_vendor=vendor,
        api_name=api_name,
        api_version=version,
        active=active,
    )
    async with control_db.session() as session:
        session.add(credential)
        await session.commit()
        return credential.id


async def _bind(
    admin_db: DatabaseSession,
    *,
    agent_id: str,
    credential_id: str,
    suspended: bool = False,
    rule_set_id: str | None = None,
) -> None:
    async with admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"),
                agent_id=agent_id,
                credential_id=credential_id,
                suspended=suspended,
                rule_set_id=rule_set_id,
            )
        )
        await session.commit()


def test_satisfies_protocol() -> None:
    assert issubclass(CredentialBindingResolver, CredentialDeriverProtocol)


async def test_unbound_agent_returns_empty_with_api_served(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """No bindings at all → empty, agent_bound_any False, api_served True."""
    await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.agent_bound_any is False
    assert result.api_served is True
    assert result.identity_mismatch is None


async def test_nothing_serves_api(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """No credential covers the API at all → api_served False (unserved emit at caller)."""
    agent_id = await _seed_agent(admin_db)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.agent_bound_any is False
    assert result.api_served is False


async def test_single_binding_match_carries_rule_set_id(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """One active binding covering the API → one candidate with its rule_set_id."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    other_id = await _seed_credential(
        control_db, name="other", vendor="other.com", api_name="x", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id, rule_set_id="prs_shared01")
    await _bind(admin_db, agent_id=agent_id, credential_id=other_id)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert len(result.credentials) == 1
    assert result.credentials[0].credential_id == cred_id
    assert result.credentials[0].rule_set_id == "prs_shared01"
    assert result.agent_bound_any is True
    assert result.api_served is True


async def test_multiple_bindings_match_sorted(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Two covering bindings → both candidates, deterministically ordered."""
    cred_a = await _seed_credential(
        control_db, name="acme-a", vendor="acme.com", api_name="pets-api", version="v1"
    )
    cred_b = await _seed_credential(
        control_db, name="acme-b", vendor="acme.com", api_name=None, version=None
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_a)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_b)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert [bc.credential_id for bc in result.credentials] == sorted([cred_a, cred_b])


async def test_suspended_binding_excluded(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A suspended binding must not authorize — the reversible per-consumer cut-off."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id, suspended=True)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    # A suspended binding does not count as "bound" for directive purposes —
    # the active-binding query excludes it entirely.
    assert result.agent_bound_any is False
    assert result.api_served is True


async def test_inactive_credential_excluded(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A disabled credential never surfaces as a candidate (nor as api_served)."""
    cred_id = await _seed_credential(
        control_db,
        name="acme",
        vendor="acme.com",
        api_name="pets-api",
        version="v1",
        active=False,
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.agent_bound_any is True
    assert result.api_served is False


async def test_nearest_miss_same_vendor(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Bound + nothing serves + same-vendor near-miss → identity_mismatch populated."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="old-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.agent_bound_any is True
    assert result.api_served is False
    assert result.identity_mismatch is not None
    assert result.identity_mismatch.found_name == "old-api"
    assert result.identity_mismatch.expected_name == "pets-api"


async def test_unrelated_vendor_is_not_a_mismatch(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Bound only to another vendor's credential → plain no-binding, no mismatch."""
    cred_id = await _seed_credential(
        control_db, name="slack", vendor="slack.com", api_name="chat", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.identity_mismatch is None


async def test_wildcard_credential_covers(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A NULL-axis (vendor-wide) credential covers any concrete operation identity."""
    cred_id = await _seed_credential(
        control_db, name="acme-wide", vendor="acme.com", api_name=None, version=None
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert [bc.credential_id for bc in result.credentials] == [cred_id]
