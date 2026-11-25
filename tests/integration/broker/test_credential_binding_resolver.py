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
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
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
    state: str = "connected",
) -> str:
    credential = Credential(
        type="token_value",
        name=name,
        api_vendor=vendor,
        api_name=api_name,
        api_version=version,
        active=active,
        state=state,
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
    expires_at: datetime | None = None,
    max_idle_period: int | None = None,
    last_used_at: datetime | None = None,
    renewed_at: datetime | None = None,
    max_quota: int | None = None,
    quota_remaining: int | None = None,
    after_expiry: str = "hold",
) -> None:
    async with admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"),
                agent_id=agent_id,
                credential_id=credential_id,
                suspended=suspended,
                rule_set_id=rule_set_id,
                expires_at=expires_at,
                max_idle_period=max_idle_period,
                last_used_at=last_used_at,
                renewed_at=renewed_at,
                max_quota=max_quota,
                quota_remaining=quota_remaining,
                after_expiry=after_expiry,
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


async def test_pending_covering_binding_is_not_an_identity_mismatch(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A bound pending credential asks the caller to finish connecting it."""
    cred_id = await _seed_credential(
        control_db,
        name="acme",
        vendor="acme.com",
        api_name="pets-api",
        version="v1",
        state="pending",
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    resolver = CredentialBindingResolver(admin_db, control_db)
    result = await resolver.derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.binding_pending is True
    assert result.identity_mismatch is None


_NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _clock() -> datetime:
    return _NOW


async def test_future_expiry_authorizes_and_sets_valid_until(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A binding that has not yet expired authorizes, and caps the derivation's lifetime."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    expiry = _NOW + timedelta(hours=1)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id, expires_at=expiry)

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert [bc.credential_id for bc in result.credentials] == [cred_id]
    assert result.valid_until == expiry
    assert result.binding_expired is False


async def test_no_expiry_has_no_valid_until(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id)

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert len(result.credentials) == 1
    assert result.valid_until is None


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=-1), timedelta(days=-30)])
async def test_expired_binding_is_not_granted_at_or_after_expiry(
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    clean_tables: None,
    offset: timedelta,
) -> None:
    """``expires_at <= now`` is expired: the boundary instant itself is already refused."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_id, expires_at=_NOW + offset)

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.binding_expired is True
    assert result.binding_suspended is False
    assert result.valid_until is None


async def test_suspended_and_expired_reports_suspended_only(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A suspended binding that has also expired is reported as suspended, not expired."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_id,
        suspended=True,
        expires_at=_NOW - timedelta(days=1),
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.binding_suspended is True
    assert result.binding_expired is False


async def test_expired_binding_for_unrelated_api_is_not_flagged(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    other = await _seed_credential(
        control_db, name="other", vendor="other.com", api_name="x-api", version="v1"
    )
    await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db, agent_id=agent_id, credential_id=other, expires_at=_NOW - timedelta(days=1)
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.binding_expired is False


async def test_valid_until_is_earliest_candidate_expiry_and_expired_sibling_dropped(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Only unexpired covering bindings are candidates; valid_until is the earliest."""
    cred_soon = await _seed_credential(
        control_db, name="soon", vendor="acme.com", api_name="pets-api", version="v1"
    )
    cred_later = await _seed_credential(
        control_db, name="later", vendor="acme.com", api_name="pets-api", version="v1"
    )
    cred_never = await _seed_credential(
        control_db, name="never", vendor="acme.com", api_name="pets-api", version="v1"
    )
    cred_gone = await _seed_credential(
        control_db, name="gone", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    soon = _NOW + timedelta(minutes=5)
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_soon, expires_at=soon)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_later,
        expires_at=_NOW + timedelta(days=2),
    )
    await _bind(admin_db, agent_id=agent_id, credential_id=cred_never)
    await _bind(
        admin_db, agent_id=agent_id, credential_id=cred_gone, expires_at=_NOW - timedelta(hours=1)
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert {bc.credential_id for bc in result.credentials} == {cred_soon, cred_later, cred_never}
    assert result.valid_until == soon
    # A usable sibling means this is not a denial, so the expired flag stays off.
    assert result.binding_expired is False


async def test_cached_candidates_observe_a_deadline_changed_on_another_node(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    cred = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent, credential_id=cred)
    resolver = CredentialBindingResolver(admin_db, control_db, clock=_clock)
    cache = CachingCredentialDeriver(
        resolver, clock=_clock, candidates_live=resolver.cached_candidates_live
    )
    args = {"agent_id": agent, "vendor": "acme.com", "name": "pets-api", "version": "v1"}
    assert (await cache.derive_credentials(**args)).credentials
    async with admin_db.transaction() as session:
        await session.execute(
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent)
            .values(expires_at=_NOW)
        )
    assert (await cache.derive_credentials(**args)).binding_expired


async def test_idle_limit_caps_valid_until_while_in_use(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A binding used within its idle limit authorizes until one idle period after that use."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    used = _NOW - timedelta(days=3)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_id,
        max_idle_period=7 * 86_400,
        last_used_at=used,
        renewed_at=_NOW - timedelta(days=30),
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert [bc.credential_id for bc in result.credentials] == [cred_id]
    assert result.valid_until == used + timedelta(days=7)


async def test_idle_ended_binding_is_refused_and_says_why(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A binding never used within its idle limit ends one idle period after it was granted."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_id,
        max_idle_period=86_400,
        renewed_at=_NOW - timedelta(days=1),
        expires_at=_NOW + timedelta(days=30),
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.binding_expired is True
    assert result.expired_reason == "idle"
    assert result.expired_credential_id == cred_id


async def test_a_use_count_is_never_cached(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Every call must reach the counter, so the derivation is never cached.

    Its time limits still bound it: ``valid_until`` is the end date, not now.
    """
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_id,
        max_quota=1,
        quota_remaining=1,
        expires_at=_NOW + timedelta(days=30),
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert [bc.credential_id for bc in result.credentials] == [cred_id]
    assert result.cacheable is False
    assert result.valid_until == _NOW + timedelta(days=30)


async def test_a_used_up_binding_is_refused_and_says_why(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_id,
        max_quota=1,
        quota_remaining=0,
        last_used_at=_NOW - timedelta(minutes=5),
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.credentials == ()
    assert result.binding_expired is True
    assert result.expired_reason == "uses"
    assert result.expired_credential_id == cred_id


@pytest.mark.parametrize(("after_expiry", "holds"), [("hold", True), ("refuse", False)])
async def test_an_ended_binding_says_whether_it_holds_the_call(
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    clean_tables: None,
    after_expiry: str,
    holds: bool,
) -> None:
    """``after_expiry`` decides whether the execute route files a renewal hold."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=cred_id,
        expires_at=_NOW - timedelta(minutes=1),
        after_expiry=after_expiry,
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.binding_expired is True
    assert result.expired_credential_id == cred_id
    assert result.expired_holds is holds


async def test_an_ended_binding_is_never_cached(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A denial that can file a renewal hold must be read live, so it is not kept."""
    cred_id = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(
        admin_db, agent_id=agent_id, credential_id=cred_id, expires_at=_NOW - timedelta(minutes=1)
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.binding_expired is True
    assert result.cacheable is False


async def test_a_binding_renewed_elsewhere_is_seen_through_the_cache(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A renewal another broker's cache never heard of must not leave a call held for it."""
    cred = await _seed_credential(
        control_db, name="acme", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent = await _seed_agent(admin_db)
    await _bind(
        admin_db, agent_id=agent, credential_id=cred, expires_at=_NOW - timedelta(minutes=1)
    )
    resolver = CredentialBindingResolver(admin_db, control_db, clock=_clock)
    cache = CachingCredentialDeriver(
        resolver, clock=_clock, candidates_live=resolver.cached_candidates_live
    )
    args = {"agent_id": agent, "vendor": "acme.com", "name": "pets-api", "version": "v1"}
    assert (await cache.derive_credentials(**args)).binding_expired
    async with admin_db.transaction() as session:
        await session.execute(
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent)
            .values(expires_at=_NOW + timedelta(days=30))
        )

    renewed = await cache.derive_credentials(**args)

    assert [bc.credential_id for bc in renewed.credentials] == [cred]
    assert renewed.binding_expired is False


async def test_an_ended_binding_is_listed_beside_a_live_one_without_being_the_denial(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A call naming the ended one can be told it ended; nothing else about the derivation moves."""
    live = await _seed_credential(
        control_db, name="live", vendor="acme.com", api_name="pets-api", version="v1"
    )
    ended = await _seed_credential(
        control_db, name="ended", vendor="acme.com", api_name="pets-api", version="v1"
    )
    agent_id = await _seed_agent(admin_db)
    await _bind(admin_db, agent_id=agent_id, credential_id=live)
    await _bind(
        admin_db,
        agent_id=agent_id,
        credential_id=ended,
        expires_at=_NOW - timedelta(minutes=1),
    )

    result = await CredentialBindingResolver(admin_db, control_db, clock=_clock).derive_credentials(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert [bc.credential_id for bc in result.credentials] == [live]
    assert [e.credential_id for e in result.ended_bindings] == [ended]
    assert result.binding_expired is False
    assert result.expired_credential_id is None
    assert result.cacheable is False
