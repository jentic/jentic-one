"""Integration tests for the cross-DB ``CredentialBindingResolver`` (theme-5 Phase 2).

Seeds the admin DB (direct agent→credential bindings) and the control DB
(credentials), then runs the broker's derivation — ``derive_bindings``, the live
``CredentialRepository.list_covering`` read, and ``diagnose_unserved`` when that
is empty — and asserts the candidates for an API identity: ``()`` (no overlap /
suspended / inactive), one candidate (with its ``rule_set_id``), and many
candidates — plus the diagnosis (``agent_bound_any`` / ``api_served`` /
nearest-miss) the broker maps to the right denial directive.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import CredentialRepository
from jentic_one.shared.broker.protocols import (
    AgentBindings,
    CredentialDeriverProtocol,
    UnservedDiagnosis,
)
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


@dataclass(frozen=True)
class _Derived:
    candidates: tuple[str, ...]
    bindings: AgentBindings
    diagnosis: UnservedDiagnosis | None


async def _derive(
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    *,
    agent_id: str,
    vendor: str = "acme.com",
    name: str = "pets-api",
    version: str = "v1",
) -> _Derived:
    """The broker's derivation: bindings ∩ live covering read, diagnosed when empty."""
    resolver = CredentialBindingResolver(admin_db, control_db)
    bindings = await resolver.derive_bindings(agent_id=agent_id)
    async with control_db.read_session() as session:
        rows = await CredentialRepository.list_covering(
            session,
            credential_ids=bindings.active_ids,
            vendor=vendor,
            name=name,
            version=version,
        )
    candidates = tuple(c.id for c in rows)
    diagnosis = (
        None
        if candidates
        else await resolver.diagnose_unserved(
            bindings=bindings, vendor=vendor, name=name, version=version
        )
    )
    return _Derived(candidates=candidates, bindings=bindings, diagnosis=diagnosis)


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

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == ()
    assert result.diagnosis is not None
    assert result.diagnosis.agent_bound_any is False
    assert result.diagnosis.api_served is True
    assert result.diagnosis.identity_mismatch is None


async def test_nothing_serves_api(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """No credential covers the API at all → api_served False (unserved emit at caller)."""
    agent_id = await _seed_agent(admin_db)

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == ()
    assert result.diagnosis is not None
    assert result.diagnosis.agent_bound_any is False
    assert result.diagnosis.api_served is False


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

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == (cred_id,)
    assert result.bindings.rule_set_id(cred_id) == "prs_shared01"
    assert result.diagnosis is None


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

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == tuple(sorted([cred_a, cred_b]))


async def test_suspended_binding_excluded(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A suspended binding must not authorize — the reversible per-consumer cut-off."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id, suspended=True)

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == ()
    assert result.bindings.suspended_ids == frozenset({cred_id})
    # A suspended binding does not count as "bound" for directive purposes —
    # the active-binding query excludes it entirely.
    assert result.diagnosis is not None
    assert result.diagnosis.agent_bound_any is False
    assert result.diagnosis.api_served is True
    assert result.diagnosis.binding_suspended is True


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

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == ()
    assert result.diagnosis is not None
    assert result.diagnosis.agent_bound_any is True
    assert result.diagnosis.api_served is False


async def test_nearest_miss_same_vendor(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Bound + nothing serves + same-vendor near-miss → identity_mismatch populated."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="old-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == ()
    assert result.diagnosis is not None
    assert result.diagnosis.agent_bound_any is True
    assert result.diagnosis.api_served is False
    assert result.diagnosis.identity_mismatch is not None
    assert result.diagnosis.identity_mismatch.found_name == "old-api"
    assert result.diagnosis.identity_mismatch.expected_name == "pets-api"


async def test_unrelated_vendor_is_not_a_mismatch(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Bound only to another vendor's credential → plain no-binding, no mismatch."""
    cred_id = await _seed_credential(
        control_db, name="slack", vendor="slack.com", api_name="chat", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == ()
    assert result.diagnosis is not None
    assert result.diagnosis.identity_mismatch is None


async def test_wildcard_credential_covers(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A NULL-axis (vendor-wide) credential covers any concrete operation identity."""
    cred_id = await _seed_credential(
        control_db, name="acme-wide", vendor="acme.com", api_name=None, version=None
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    result = await _derive(admin_db, control_db, agent_id=agent_id)

    assert result.candidates == (cred_id,)
