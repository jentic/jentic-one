"""Integration tests for the theme-8 Phase 1 service-account → agent migration.

Runs ``ServiceAccountMigrationService`` against real admin (+ control)
databases on both dialects (``JENTIC_TEST_BACKEND=sqlite`` locally, Postgres
in CI): the copy→revoke→stamp→audit transaction, dispositions (OQ-1),
resolver behaviour through the window (agent arm, SA fallback, post-sweep),
the deferred sweep (W3/N3), and verify/acknowledge
(W9). Test names lift the plan's acceptance criteria verbatim where they
apply.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import structlog
from sqlalchemy import text

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.service_account_credentials import ServiceAccountCredential
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.repos.agent_credential_repo import AgentCredentialRepository
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.service_account_migration_repo import (
    ServiceAccountMigrationRepository,
)
from jentic_one.control.services.service_account_migration import (
    ServiceAccountMigrationService,
)
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType

pytestmark = pytest.mark.integration

_OWNER = "usr_t8m_owner"


@pytest.fixture()
async def clean_tables(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove every row this module seeds or the job creates, before and after.

    The job scans **every** service account, so stray rows from other modules
    would leak into the outcome list — wipe them all.
    """

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            successor_filter = (
                "(SELECT id FROM agents WHERE registered_by = 'system:theme8-sa-migration')"
            )
            for table, column in (
                ("agent_credential_bindings", "agent_id"),
                ("actor_scope_grants", "actor_id"),
                ("agent_credentials", "agent_id"),
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE {column} IN {successor_filter}")
                )
            await session.execute(
                text("DELETE FROM agents WHERE registered_by = 'system:theme8-sa-migration'")
            )
            for table, column in (
                ("actor_scope_grants", "actor_id"),
                ("agent_credential_bindings", "agent_id"),
                ("access_tokens", "actor_id"),
                ("refresh_tokens", "actor_id"),
                ("service_account_credentials", "service_account_id"),
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE {column} IN (SELECT id FROM service_accounts)")
                )
            await session.execute(text("DELETE FROM service_accounts"))
            await session.execute(text("DELETE FROM service_account_migration_acks"))
            await session.execute(
                text("DELETE FROM audit_entries WHERE actor_id = 'migrate-service-accounts'")
            )
            await session.execute(text("DELETE FROM users WHERE id = :owner"), {"owner": _OWNER})
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture()
async def seed_owner(admin_db: DatabaseSession, clean_tables: None) -> None:
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 't8m-owner@test.local', 'Tia', 'Owner') ON CONFLICT DO NOTHING"
            ),
            {"id": _OWNER},
        )
        await session.commit()


def _digest(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def _seed_sa(
    admin_db: DatabaseSession,
    *,
    suffix: str,
    status: str = "active",
    scopes: tuple[str, ...] = (),
    api_key_plaintext: str | None = None,
    client_secret_hash: str | None = None,
    with_tokens: bool = False,
    credential_ids: tuple[str, ...] = (),
) -> str:
    """Seed one service account with the requested satellites; return its id."""
    sa_id = f"sva_t8m_{suffix}"
    now = dt.datetime.now(dt.UTC)
    async with admin_db.session() as session:
        session.add(
            ServiceAccount(
                id=sa_id,
                name=f"t8m-{suffix}",
                owner_id=_OWNER,
                registered_by=_OWNER,
                status=status,
                created_by=_OWNER,
            )
        )
        await session.flush()
        session.add(
            ServiceAccountCredential(
                id=f"sac_t8m_{suffix}",
                service_account_id=sa_id,
                api_key_hash=_digest(api_key_plaintext) if api_key_plaintext else None,
                client_secret_hash=client_secret_hash,
                created_by=_OWNER,
            )
        )
        for scope in scopes:
            await session.execute(
                text(
                    "INSERT INTO actor_scope_grants"
                    " (id, actor_id, actor_type, scope, granted_by, created_by)"
                    " VALUES (:id, :actor_id, 'service_account', :scope, :by, :by)"
                ),
                {
                    "id": f"asg_t8m_{suffix}_{scope[:8]}",
                    "actor_id": sa_id,
                    "scope": scope,
                    "by": _OWNER,
                },
            )
        for credential_id in credential_ids:
            await session.execute(
                text(
                    "INSERT INTO agent_credential_bindings"
                    " (id, agent_id, credential_id, created_by)"
                    " VALUES (:id, :agent_id, :credential_id, :by)"
                ),
                {
                    "id": f"acb_t8m_{suffix}_{credential_id[-6:]}",
                    "agent_id": sa_id,
                    "credential_id": credential_id,
                    "by": _OWNER,
                },
            )
        if with_tokens:
            session.add(
                AccessToken(
                    id=f"at_t8m_{suffix}",
                    token_hash=_digest(f"at_t8m_{suffix}"),
                    actor_id=sa_id,
                    actor_type="service_account",
                    scopes=list(scopes),
                    token_family_id=f"tf_t8m_{suffix}",
                    expires_at=now + dt.timedelta(hours=1),
                    created_by=_OWNER,
                )
            )
            session.add(
                RefreshToken(
                    id=f"rt_t8m_{suffix}",
                    token_hash=_digest(f"rt_t8m_{suffix}"),
                    actor_id=sa_id,
                    actor_type="service_account",
                    scopes=list(scopes),
                    token_family_id=f"tf_t8m_{suffix}",
                    expires_at=now + dt.timedelta(days=7),
                    created_by=_OWNER,
                )
            )
        await session.commit()
    return sa_id


async def _rows(admin_db: DatabaseSession, query: str, params: dict[str, object]) -> list[Any]:
    async with admin_db.session() as session:
        return list((await session.execute(text(query), params)).all())


async def _stamp_of(admin_db: DatabaseSession, sa_id: str) -> tuple[str | None, object]:
    rows = await _rows(
        admin_db,
        "SELECT migrated_to_actor_id, migrated_at FROM service_accounts WHERE id = :id",
        {"id": sa_id},
    )
    assert len(rows) == 1
    return rows[0].migrated_to_actor_id, rows[0].migrated_at


# --------------------------------------------------------------- job + window


async def test_active_sa_full_migration_copies_everything_and_stamps(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """Copy→revoke→stamp→audit for an active SA, satellite by satellite."""
    plaintext = "sak_t8m_full_key"
    sa_id = await _seed_sa(
        admin_db,
        suffix="full",
        scopes=("capabilities:execute", "toolkit:read", "service-accounts:read"),
        api_key_plaintext=plaintext,
        client_secret_hash="cs-digest",
        with_tokens=True,
        credential_ids=("cred_t8m_full",),
    )

    outcomes = await ServiceAccountMigrationService(integration_context).run()

    outcome = {o.service_account_id: o for o in outcomes}[sa_id]
    assert outcome.outcome == "migrated"
    agent_id = outcome.successor_agent_id
    assert agent_id is not None and agent_id.startswith("agnt_")
    assert outcome.stored_scope_count == 2  # retired service-accounts:read not carried
    assert outcome.credential_binding_count == 1
    assert outcome.access_tokens_revoked == 1
    assert outcome.refresh_tokens_revoked == 1
    assert outcome.had_client_secret is True
    assert outcome.owner_visibility_note is not None

    # Successor agent: active, owned by the SA's owner, system-registered.
    agents = await _rows(
        admin_db,
        "SELECT status, owner_id, registered_by FROM agents WHERE id = :id",
        {"id": agent_id},
    )
    assert [(r.status, r.owner_id) for r in agents] == [("active", _OWNER)]
    assert agents[0].registered_by == "system:theme8-sa-migration"

    # Digest COPIED (both sides live — copy-then-sweep, H-B).
    agent_digests = await _rows(
        admin_db,
        "SELECT api_key_hash FROM agent_credentials WHERE agent_id = :id",
        {"id": agent_id},
    )
    assert [r.api_key_hash for r in agent_digests] == [_digest(plaintext)]
    sa_digests = await _rows(
        admin_db,
        "SELECT api_key_hash FROM service_account_credentials WHERE service_account_id = :id",
        {"id": sa_id},
    )
    assert [r.api_key_hash for r in sa_digests] == [_digest(plaintext)]

    # Grant twins: stored rows only, retired scopes left behind, originals kept.
    agent_grants = await _rows(
        admin_db,
        "SELECT scope FROM actor_scope_grants"
        " WHERE actor_id = :id AND actor_type = 'agent' ORDER BY scope",
        {"id": agent_id},
    )
    assert [r.scope for r in agent_grants] == ["capabilities:execute", "toolkit:read"]
    sa_grants = await _rows(
        admin_db,
        "SELECT scope FROM actor_scope_grants"
        " WHERE actor_id = :id AND actor_type = 'service_account'",
        {"id": sa_id},
    )
    assert len(sa_grants) == 3  # untouched until the sweep (N1)

    # Binding twin, coexisting with the sva_-keyed original.
    twins = await _rows(
        admin_db,
        "SELECT id FROM agent_credential_bindings WHERE agent_id = :id",
        {"id": agent_id},
    )
    originals = await _rows(
        admin_db,
        "SELECT id FROM agent_credential_bindings WHERE agent_id = :id",
        {"id": sa_id},
    )
    assert len(twins) == 1
    assert len(originals) == 1

    # Opaque sessions dead (H-1).
    for table in ("access_tokens", "refresh_tokens"):
        tokens = await _rows(
            admin_db,
            f"SELECT revoked_at FROM {table} WHERE actor_id = :id",
            {"id": sa_id},
        )
        assert len(tokens) == 1 and tokens[0].revoked_at is not None, table

    # Stamp pair written.
    stamp, migrated_at = await _stamp_of(admin_db, sa_id)
    assert stamp == agent_id
    assert migrated_at is not None


async def test_every_migrated_sa_has_register_grant_revoke_audit_rows(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    sa_id = await _seed_sa(
        admin_db, suffix="audit", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_audit"
    )

    outcomes = await ServiceAccountMigrationService(integration_context).run()
    agent_id = {o.service_account_id: o for o in outcomes}[sa_id].successor_agent_id

    audit_rows = await _rows(
        admin_db,
        "SELECT action, target_id, actor_type, origin FROM audit_entries"
        " WHERE actor_id = 'migrate-service-accounts'"
        " AND target_id IN (:agent_id, :sa_id) ORDER BY action",
        {"agent_id": agent_id, "sa_id": sa_id},
    )
    by_action = {(r.action, r.target_id) for r in audit_rows}
    assert ("register", agent_id) in by_action
    assert ("grant", agent_id) in by_action
    assert ("revoke", sa_id) in by_action
    assert all(r.actor_type == "system:job" for r in audit_rows)
    assert all(r.origin == "system" for r in audit_rows)


async def test_rerun_is_noop_via_stamp_short_circuit(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    sa_id = await _seed_sa(
        admin_db, suffix="idem", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_idem"
    )
    svc = ServiceAccountMigrationService(integration_context)

    first = {o.service_account_id: o for o in await svc.run()}[sa_id]
    second = {o.service_account_id: o for o in await svc.run()}[sa_id]

    assert first.outcome == "migrated"
    assert second.outcome == "already_migrated"
    assert second.successor_agent_id == first.successor_agent_id
    successors = await _rows(
        admin_db,
        "SELECT id FROM agents WHERE registered_by = 'system:theme8-sa-migration'",
        {},
    )
    assert len(successors) == 1  # no double mint


async def test_skip_but_stamp_revokes_outstanding_tokens_too(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """M5: a skip-but-stamp SA (no successor) still gets its opaque sessions
    family-revoked in the stamp transaction — verify criterion 3 counts every
    SA row and would otherwise fail unfixably."""
    sa_id = await _seed_sa(
        admin_db, suffix="skiptok", status="pending", scopes=("toolkit:read",), with_tokens=True
    )

    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}

    outcome = outcomes[sa_id]
    assert outcome.outcome == "skipped-non-active"
    assert outcome.successor_agent_id is None
    assert outcome.access_tokens_revoked == 1
    assert outcome.refresh_tokens_revoked == 1
    for table in ("access_tokens", "refresh_tokens"):
        tokens = await _rows(
            admin_db, f"SELECT revoked_at FROM {table} WHERE actor_id = :id", {"id": sa_id}
        )
        assert len(tokens) == 1 and tokens[0].revoked_at is not None, table

    result = await svc.verify()
    assert result.unrevoked_token_count == 0
    assert result.passed is True


async def test_two_concurrent_sessions_race_one_unstamped_sa(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """L1: a REAL two-session race on one unstamped SA — both sessions run
    the full copy→revoke→stamp transaction concurrently; exactly one wins,
    the loser reports cleanly, and no partial write survives."""
    plaintext = "sak_t8m_realrace"
    sa_id = await _seed_sa(
        admin_db, suffix="realrace", scopes=("toolkit:read",), api_key_plaintext=plaintext
    )
    async with admin_db.session() as session:
        rows = await ServiceAccountMigrationRepository.list_service_accounts(session)
    row = next(r for r in rows if r.id == sa_id)
    assert row.migrated_to_actor_id is None

    svc_a = ServiceAccountMigrationService(integration_context)
    svc_b = ServiceAccountMigrationService(integration_context)
    outcome_a, outcome_b = await asyncio.gather(svc_a._migrate_one(row), svc_b._migrate_one(row))

    results = sorted((outcome_a.outcome, outcome_b.outcome))
    winners = [o for o in (outcome_a, outcome_b) if o.outcome == "migrated"]
    assert len(winners) == 1, results
    loser = next(o for o in (outcome_a, outcome_b) if o is not winners[0])
    assert loser.outcome in {"already_migrated", "failed"}, results

    # Exactly one successor, one credential row, stamp points at the winner.
    successors = await _rows(
        admin_db,
        "SELECT id FROM agents WHERE registered_by = 'system:theme8-sa-migration'",
        {},
    )
    assert len(successors) == 1
    digests = await _rows(
        admin_db,
        "SELECT id FROM agent_credentials WHERE api_key_hash = :h",
        {"h": _digest(plaintext)},
    )
    assert len(digests) == 1
    stamp, _ = await _stamp_of(admin_db, sa_id)
    assert stamp == winners[0].successor_agent_id


async def test_unexpected_row_error_is_isolated_and_the_loop_continues(
    integration_context: Context,
    admin_db: DatabaseSession,
    seed_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2: an arbitrary per-row failure (not just the two anticipated types)
    reports ``failed`` and never aborts the run for the remaining SAs."""
    poisoned = await _seed_sa(
        admin_db, suffix="err_a", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_err_a"
    )
    healthy = await _seed_sa(admin_db, suffix="err_b", api_key_plaintext="sak_t8m_err_b")

    original = ServiceAccountMigrationRepository.copy_scope_grants

    async def _poisoned_copy(
        session: Any, *, service_account_id: str, agent_id: str
    ) -> list[tuple[str, str | None]]:
        if service_account_id == poisoned:
            raise RuntimeError("simulated malformed row")
        return await original(session, service_account_id=service_account_id, agent_id=agent_id)

    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_scope_grants", _poisoned_copy)

    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run()
    }

    assert outcomes[poisoned].outcome == "failed"
    assert outcomes[poisoned].reason == "error:RuntimeError"
    assert outcomes[healthy].outcome == "migrated"
    # The poisoned row rolled back whole: no stamp, no successor remnant.
    stamp, _ = await _stamp_of(admin_db, poisoned)
    assert stamp is None


async def test_zero_grant_sa_yields_zero_grant_successor_and_never_pending(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """F1: raw SQL bypasses DEFAULT_AGENT_SCOPES — empty stays empty."""
    sa_id = await _seed_sa(admin_db, suffix="zero", api_key_plaintext="sak_t8m_zero")

    outcomes = await ServiceAccountMigrationService(integration_context).run()
    outcome = {o.service_account_id: o for o in outcomes}[sa_id]

    assert outcome.outcome == "migrated"
    assert outcome.stored_scope_count == 0
    agents = await _rows(
        admin_db, "SELECT status FROM agents WHERE id = :id", {"id": outcome.successor_agent_id}
    )
    assert [r.status for r in agents] == ["active"]  # never pending (OQ-1)
    grants = await _rows(
        admin_db,
        "SELECT scope FROM actor_scope_grants WHERE actor_id = :id",
        {"id": outcome.successor_agent_id},
    )
    assert grants == []


async def test_no_successor_holds_stored_grant_row_its_sa_did_not(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    await _seed_sa(admin_db, suffix="ga", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_ga")
    await _seed_sa(admin_db, suffix="gb", api_key_plaintext="sak_t8m_gb")

    await ServiceAccountMigrationService(integration_context).run()

    excess = await _rows(
        admin_db,
        "SELECT g.scope FROM actor_scope_grants g"
        " JOIN service_accounts sa ON sa.migrated_to_actor_id = g.actor_id"
        " WHERE g.actor_type = 'agent'"
        " AND NOT EXISTS (SELECT 1 FROM actor_scope_grants o"
        "  WHERE o.actor_id = sa.id AND o.actor_type = 'service_account'"
        "  AND o.scope = g.scope)",
        {},
    )
    assert excess == []


# ------------------------------------------------------ resolver, dispositions


async def test_migrated_sak_key_authenticates_with_identical_effective_scopes_on_agent_arm(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    plaintext = "sak_t8m_resolve"
    sa_id = await _seed_sa(
        admin_db,
        suffix="resolve",
        scopes=("capabilities:execute", "toolkit:read"),
        api_key_plaintext=plaintext,
    )
    resolver = ApiKeyResolver(admin_db)
    before = await resolver.resolve(plaintext)
    assert before is not None and before.actor_type is ActorType.SERVICE_ACCOUNT

    outcomes = await ServiceAccountMigrationService(integration_context).run()
    agent_id = {o.service_account_id: o for o in outcomes}[sa_id].successor_agent_id

    after = await resolver.resolve(plaintext)
    assert after is not None
    assert after.sub == agent_id
    assert after.actor_type is ActorType.AGENT
    assert sorted(after.permissions) == sorted(before.permissions)


async def test_migrated_key_still_resolves_via_unmodified_sa_arm_until_sweep(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """Rolling-upgrade honesty (H-B): simulate an old-image pod by calling the
    SA arm directly — the digest and the grant rows must still be live, with
    FULL permissions (the rev-3 403-outage regression fence, T-6a)."""
    plaintext = "sak_t8m_oldpod"
    sa_id = await _seed_sa(
        admin_db,
        suffix="oldpod",
        scopes=("capabilities:execute", "toolkit:read"),
        api_key_plaintext=plaintext,
    )

    await ServiceAccountMigrationService(integration_context).run()

    resolver = ApiKeyResolver(admin_db)
    identity = await resolver._resolve_service_account(plaintext)
    assert identity is not None
    assert identity.sub == sa_id
    assert sorted(identity.permissions) == ["capabilities:execute", "toolkit:read"]


async def test_unmigrated_sak_key_resolves_identically_during_window_with_warning_and_counter(
    admin_db: DatabaseSession, seed_owner: None
) -> None:
    plaintext = "sak_t8m_unmig"
    sa_id = await _seed_sa(
        admin_db, suffix="unmig", scopes=("toolkit:read",), api_key_plaintext=plaintext
    )

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(plaintext)

    assert identity is not None
    assert identity.sub == sa_id
    assert identity.actor_type is ActorType.SERVICE_ACCOUNT
    fallbacks = [log for log in logs if log["event"] == "service_account_fallback_resolve"]
    assert len(fallbacks) == 1 and fallbacks[0]["log_level"] == "warning"


async def test_disabled_sa_successor_created_disabled_and_key_dead_until_agent_enable(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    plaintext = "sak_t8m_disabled"
    sa_id = await _seed_sa(
        admin_db, suffix="disabled", status="disabled", api_key_plaintext=plaintext
    )

    outcomes = await ServiceAccountMigrationService(integration_context).run()
    outcome = {o.service_account_id: o for o in outcomes}[sa_id]
    assert outcome.outcome == "migrated-disabled"
    agent_id = outcome.successor_agent_id
    agents = await _rows(admin_db, "SELECT status FROM agents WHERE id = :id", {"id": agent_id})
    assert [r.status for r in agents] == ["disabled"]

    resolver = ApiKeyResolver(admin_db)
    assert await resolver.resolve(plaintext) is None  # both arms refuse non-active

    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE agents SET status = 'active' WHERE id = :id"), {"id": agent_id}
        )
        await session.commit()
    revived = await resolver.resolve(plaintext)
    assert revived is not None and revived.sub == agent_id


async def test_disabling_the_successor_fails_closed_never_falls_back_to_active_sa(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """H1(a): the runbook's kill lever — disable the successor agent — must cut
    the old plaintext even though the SA row is still active (the SA-side
    disable is 409-refused by the stamp guard)."""
    plaintext = "sak_t8m_killlever"
    sa_id = await _seed_sa(
        admin_db, suffix="killlever", scopes=("toolkit:read",), api_key_plaintext=plaintext
    )
    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run()
    }
    agent_id = outcomes[sa_id].successor_agent_id

    # Operator cuts the key: disables the successor. SA row stays active.
    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE agents SET status = 'disabled' WHERE id = :id"), {"id": agent_id}
        )
        await session.commit()
    sa_status = await _rows(
        admin_db, "SELECT status FROM service_accounts WHERE id = :id", {"id": sa_id}
    )
    assert [r.status for r in sa_status] == [("active")]

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(plaintext)

    assert identity is None  # fail closed — never the SA fallback
    fail_closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(fail_closed) == 1 and fail_closed[0]["reason"] == "successor_inactive"
    # No fallback WARNING/counter: the SA arm was never consulted.
    assert [log for log in logs if log["event"] == "service_account_fallback_resolve"] == []


async def test_revoking_the_successor_key_fails_closed_on_stamped_sa(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """H1(b): revoking/rotating the successor's key NULLs the agent-side
    digest — a genuine agent-arm miss — but the stamped SA row must refuse
    regardless of its (still-active) status."""
    plaintext = "sak_t8m_revlever"
    sa_id = await _seed_sa(
        admin_db, suffix="revlever", scopes=("toolkit:read",), api_key_plaintext=plaintext
    )
    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run()
    }
    agent_id = outcomes[sa_id].successor_agent_id

    # Operator revokes the successor's key: agent-side digest is NULLed.
    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE agent_credentials SET api_key_hash = NULL WHERE agent_id = :id"),
            {"id": agent_id},
        )
        await session.commit()

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(plaintext)

    assert identity is None  # the still-live SA digest must not resurrect the key
    fail_closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(fail_closed) == 1 and fail_closed[0]["reason"] == "stamped_service_account"
    assert fail_closed[0]["service_account_id"] == sa_id


async def test_non_active_sas_are_skipped_but_stamped(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """OQ-1: pending/rejected/archived rows get the ``skipped`` stamp, no successor."""
    ids = {}
    for status in ("pending", "rejected", "archived"):
        ids[status] = await _seed_sa(admin_db, suffix=f"skip_{status}", status=status)

    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run()
    }

    for status, sa_id in ids.items():
        assert outcomes[sa_id].outcome == "skipped-non-active", status
        assert outcomes[sa_id].successor_agent_id is None
        stamp, migrated_at = await _stamp_of(admin_db, sa_id)
        assert stamp == "skipped"
        assert migrated_at is not None
    successors = await _rows(
        admin_db,
        "SELECT id FROM agents WHERE registered_by = 'system:theme8-sa-migration'",
        {},
    )
    assert successors == []


async def test_report_names_every_sa_with_client_secret_hash(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    with_secret = await _seed_sa(
        admin_db, suffix="cs", api_key_plaintext="sak_t8m_cs", client_secret_hash="digest"
    )
    without_secret = await _seed_sa(admin_db, suffix="nocs", api_key_plaintext="sak_t8m_nocs")

    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run()
    }

    assert outcomes[with_secret].had_client_secret is True
    assert outcomes[without_secret].had_client_secret is False


async def test_concurrent_double_run_mints_no_duplicate_digest_row(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """H-A x F6: a digest already present in agent_credentials fails that SA's
    whole transaction (unique partial index), reported — never a partial write,
    never a second credential row the resolver would 500 on."""
    plaintext = "sak_t8m_race"
    sa_id = await _seed_sa(admin_db, suffix="race", api_key_plaintext=plaintext)
    # Simulate the concurrent loser's view: the digest already landed on an
    # agent (what a winning parallel run's insert does).
    async with admin_db.session() as session:
        session.add(
            Agent(
                id="agnt_t8m_race_winner",
                name="race-winner",
                owner_id=_OWNER,
                registered_by="system:theme8-sa-migration",
                status="active",
                created_by="system:theme8-sa-migration",
            )
        )
        await session.flush()
        session.add(
            AgentCredential(
                id="agc_t8m_race_winner",
                agent_id="agnt_t8m_race_winner",
                api_key_hash=_digest(plaintext),
                created_by="system:theme8-sa-migration",
            )
        )
        await session.commit()

    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run()
    }

    outcome = outcomes[sa_id]
    assert outcome.outcome == "failed"
    assert outcome.reason == "integrity_error"
    # Rolled back whole: no half-created successor, stamp still NULL.
    stamp, _ = await _stamp_of(admin_db, sa_id)
    assert stamp is None
    digests = await _rows(
        admin_db,
        "SELECT id FROM agent_credentials WHERE api_key_hash = :h",
        {"h": _digest(plaintext)},
    )
    assert len(digests) == 1  # exactly one credential row survives


async def test_concurrent_winner_detected_by_in_transaction_recheck(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """A run holding a stale unstamped snapshot loses cleanly to the winner."""
    sa_id = await _seed_sa(admin_db, suffix="stale", api_key_plaintext="sak_t8m_stale")
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    assert outcomes[sa_id].outcome == "migrated"

    # Replay _migrate_one with the pre-migration (unstamped) row snapshot.
    async with admin_db.session() as session:
        rows = list(
            (
                await session.execute(
                    text(
                        "SELECT sa.id, sa.name, sa.description, sa.owner_id, sa.status,"
                        " NULL AS migrated_to_actor_id, NULL AS migrated_at,"
                        " sac.api_key_hash, sac.client_secret_hash"
                        " FROM service_accounts sa"
                        " LEFT JOIN service_account_credentials sac"
                        "  ON sac.service_account_id = sa.id"
                        " WHERE sa.id = :id"
                    ),
                    {"id": sa_id},
                )
            ).all()
        )

    replay = await svc._migrate_one(rows[0])
    assert replay.outcome == "already_migrated"
    assert replay.reason == "concurrent_run_won"


async def test_diff_only_writes_nothing(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    sa_id = await _seed_sa(
        admin_db, suffix="diff", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_diff"
    )

    outcomes = {
        o.service_account_id: o
        for o in await ServiceAccountMigrationService(integration_context).run(diff_only=True)
    }

    assert outcomes[sa_id].outcome == "migrated"  # the disposition it WOULD take
    stamp, _ = await _stamp_of(admin_db, sa_id)
    assert stamp is None
    successors = await _rows(
        admin_db,
        "SELECT id FROM agents WHERE registered_by = 'system:theme8-sa-migration'",
        {},
    )
    assert successors == []


async def test_diff_only_preview_counts_match_the_real_run(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """The preview reports what the real run WILL copy/revoke (same queries,
    retired scopes excluded), and never a misleading zero for a stamped row."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="pvcount",
        scopes=("toolkit:read", "credentials:read", "service-accounts:read"),
        api_key_plaintext="sak_t8m_pvcount",
        with_tokens=True,
        credential_ids=(rule_credential,),
    )
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    svc = ServiceAccountMigrationService(integration_context)

    preview = {o.service_account_id: o for o in await svc.run(diff_only=True)}[sa_id]
    real = {o.service_account_id: o for o in await svc.run()}[sa_id]

    assert real.outcome == "migrated"
    counted = (
        "stored_scope_count",
        "credential_binding_count",
        "permission_rule_count",
        "access_tokens_revoked",
        "refresh_tokens_revoked",
    )
    assert {f: getattr(preview, f) for f in counted} == {f: getattr(real, f) for f in counted}
    assert (preview.stored_scope_count, preview.permission_rule_count) == (2, 2)
    assert (preview.access_tokens_revoked, preview.refresh_tokens_revoked) == (1, 1)

    stamped = {o.service_account_id: o for o in await svc.run(diff_only=True)}[sa_id]
    assert stamped.outcome == "already_migrated"
    assert all(getattr(stamped, f) is None for f in counted)  # not computed


# ---------------------------------------------------------------- W3 sweep


async def test_sweep_age_gate_holds_fresh_stamps_and_override_sweeps(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    plaintext = "sak_t8m_sweep"
    sa_id = await _seed_sa(
        admin_db,
        suffix="sweep",
        scopes=("toolkit:read",),
        api_key_plaintext=plaintext,
        credential_ids=("cred_t8m_sweep",),
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id

    # Fresh stamp (default gate 24h): the automatic arm holds it back.
    gated = await svc.sweep()
    assert gated.swept == []
    assert gated.skipped_young == 1

    # Operator override ignores the gate.
    swept = await svc.sweep(ignore_age_gate=True)
    assert swept.swept == [sa_id]

    # SA-keyed originals gone, digest NULLed, row archived.
    for table, column in (
        ("actor_scope_grants", "actor_id"),
        ("agent_credential_bindings", "agent_id"),
    ):
        rows = await _rows(admin_db, f"SELECT id FROM {table} WHERE {column} = :id", {"id": sa_id})
        assert rows == [], table
    sa_rows = await _rows(
        admin_db,
        "SELECT sa.status, sac.api_key_hash FROM service_accounts sa"
        " JOIN service_account_credentials sac ON sac.service_account_id = sa.id"
        " WHERE sa.id = :id",
        {"id": sa_id},
    )
    assert [(r.status, r.api_key_hash) for r in sa_rows] == [("archived", None)]

    # ARCHIVE audit row written by the sweep.
    archives = await _rows(
        admin_db,
        "SELECT id FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND action = 'archive' AND target_id = :id",
        {"id": sa_id},
    )
    assert len(archives) == 1

    # Post-sweep: SA arm misses, agent arm still serves (T-6c).
    resolver = ApiKeyResolver(admin_db)
    assert await resolver._resolve_service_account(plaintext) is None
    identity = await resolver.resolve(plaintext)
    assert identity is not None and identity.sub == agent_id


async def test_sweep_backdated_stamp_passes_age_gate_and_skip_stamp_rows_archived(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    migrated = await _seed_sa(admin_db, suffix="aged", api_key_plaintext="sak_t8m_aged")
    skipped = await _seed_sa(admin_db, suffix="agedskip", status="pending")
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()
    backdated = dt.datetime.now(dt.UTC) - dt.timedelta(hours=48)
    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE service_accounts SET migrated_at = :ts WHERE id IN (:a, :b)"),
            {"ts": backdated, "a": migrated, "b": skipped},
        )
        await session.commit()

    outcome = await svc.sweep()  # automatic (gated) arm

    assert set(outcome.swept) == {migrated, skipped}
    statuses = await _rows(
        admin_db,
        "SELECT status FROM service_accounts WHERE id IN (:a, :b)",
        {"a": migrated, "b": skipped},
    )
    assert [r.status for r in statuses] == ["archived", "archived"]


async def test_pre_archived_stamped_sa_is_swept_and_sweep_is_idempotent(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """M2: an SA already ``archived`` at migration time (skip-but-stamp) must
    still get its lingering ``sva_``-keyed grant/binding/digest rows swept —
    they would block the Phase-4 drop. L3: the archive UPDATE no-ops (already
    archived), so NO duplicate ARCHIVE audit row is written; a second sweep
    finds nothing left to do."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="prearch",
        status="archived",
        scopes=("toolkit:read",),
        api_key_plaintext="sak_t8m_prearch",
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    assert outcomes[sa_id].outcome == "skipped-non-active"

    first = await svc.sweep(ignore_age_gate=True)
    assert sa_id in first.swept

    # Satellites gone, digest NULLed, status still archived.
    for table, column in (("actor_scope_grants", "actor_id"),):
        rows = await _rows(admin_db, f"SELECT id FROM {table} WHERE {column} = :id", {"id": sa_id})
        assert rows == [], table
    sa_rows = await _rows(
        admin_db,
        "SELECT sa.status, sac.api_key_hash FROM service_accounts sa"
        " JOIN service_account_credentials sac ON sac.service_account_id = sa.id"
        " WHERE sa.id = :id",
        {"id": sa_id},
    )
    assert [(r.status, r.api_key_hash) for r in sa_rows] == [("archived", None)]

    # L3: the row was already archived — no ARCHIVE audit row from the sweep.
    archives = await _rows(
        admin_db,
        "SELECT id FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND action = 'archive' AND target_id = :id",
        {"id": sa_id},
    )
    assert archives == []

    # Idempotent: nothing left to sweep on the second pass.
    second = await svc.sweep(ignore_age_gate=True)
    assert sa_id not in second.swept


async def test_repeated_sweeps_write_exactly_one_archive_audit_row(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """L3: the archive UPDATE's in-transaction re-check (status != 'archived')
    keeps re-sweeps from duplicating the ARCHIVE audit row."""
    sa_id = await _seed_sa(admin_db, suffix="resweep", api_key_plaintext="sak_t8m_resweep")
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    await svc.sweep(ignore_age_gate=True)
    await svc.sweep(ignore_age_gate=True)

    archives = await _rows(
        admin_db,
        "SELECT id FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND action = 'archive' AND target_id = :id",
        {"id": sa_id},
    )
    assert len(archives) == 1


# ------------------------------------------------------- W8 stamp guards (M4)


async def test_verify_counts_post_stamp_sva_binding_inserts(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """M4: verify criterion 5 counts fresh ``sva_``-keyed binding rows written
    after the stamp (raw-SQL writers bypassing the service guards)."""
    sa_id = await _seed_sa(
        admin_db, suffix="vbind", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_vbind"
    )
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    clean = await svc.verify()
    assert clean.post_stamp_mutation_count == 0

    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings"
                " (id, agent_id, credential_id, created_by, created_at)"
                " VALUES ('acb_t8m_late', :id, 'cred_t8m_late', :by, :late)"
            ),
            {"id": sa_id, "by": _OWNER, "late": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)},
        )
        await session.commit()

    result = await svc.verify()
    assert result.passed is False
    assert result.post_stamp_mutation_count == 1


# ------------------------------------------------------------ W9 verify/ack


async def test_verify_fails_on_unstamped_row_and_acknowledge_is_refused(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    await _seed_sa(admin_db, suffix="vfail", api_key_plaintext="sak_t8m_vfail")
    svc = ServiceAccountMigrationService(integration_context)

    result = await svc.verify(acknowledge=True)

    assert result.passed is False
    assert result.unstamped_count == 1
    assert result.acknowledged is False
    acks = await _rows(admin_db, "SELECT id FROM service_account_migration_acks", {})
    assert acks == []


async def test_verify_passes_after_migration_and_acknowledge_writes_sentinel(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    await _seed_sa(
        admin_db,
        suffix="vpass",
        scopes=("toolkit:read",),
        api_key_plaintext="sak_t8m_vpass",
        with_tokens=True,
    )
    await _seed_sa(admin_db, suffix="vpass_skip", status="rejected")
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    result = await svc.verify(acknowledge=True)

    assert result.passed is True
    assert result.acknowledged is True
    acks = await _rows(
        admin_db,
        "SELECT unstamped_count, grant_twin_missing_count, unrevoked_token_count,"
        " digest_mismatch_count, post_stamp_mutation_count, report_finding_count,"
        " tool_version"
        " FROM service_account_migration_acks",
        {},
    )
    assert len(acks) == 1
    assert (
        acks[0].unstamped_count,
        acks[0].grant_twin_missing_count,
        acks[0].unrevoked_token_count,
        acks[0].digest_mismatch_count,
        acks[0].post_stamp_mutation_count,
        acks[0].report_finding_count,  # the verify summary is not a finding
    ) == (0, 0, 0, 0, 0, 0)
    assert acks[0].tool_version
    # ...but it stays in the report.
    assert [f["category"] for f in result.findings] == ["verify_summary"]


async def test_verify_fails_on_missing_grant_twin_and_post_stamp_mutation(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    sa_id = await _seed_sa(
        admin_db, suffix="vtwin", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_vtwin"
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id

    # Break criterion 2 (delete the twin) and criterion 5 (post-stamp grant).
    async with admin_db.session() as session:
        await session.execute(
            text("DELETE FROM actor_scope_grants WHERE actor_id = :id"), {"id": agent_id}
        )
        await session.execute(
            text(
                "INSERT INTO actor_scope_grants"
                " (id, actor_id, actor_type, scope, granted_by, created_by, created_at)"
                " VALUES ('asg_t8m_late', :id, 'service_account', 'sneaky:scope',"
                " :by, :by, :late)"
            ),
            {"id": sa_id, "by": _OWNER, "late": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)},
        )
        await session.commit()

    result = await svc.verify()

    assert result.passed is False
    assert result.grant_twin_missing_count >= 1
    assert result.post_stamp_mutation_count >= 1


# ------------------------------------ criterion 4: superseded successors (#1416)


def _findings(result: Any, category: str) -> list[dict[str, Any]]:
    return [f for f in result.findings if f["category"] == category]


@pytest.mark.parametrize("regenerate", [True, False], ids=["rotated", "revoked"])
async def test_verify_passes_after_successor_key_rotation_and_sweep_clears_the_note(
    integration_context: Context,
    admin_db: DatabaseSession,
    seed_owner: None,
    regenerate: bool,
) -> None:
    """#1416: an operator rotating (revoke + regenerate) or just revoking the
    successor's key after the stamp is not drift — verify passes, the ack is
    recorded, and the superseded SA digest is reported informationally with
    the sweep hint until ``--sweep-migrated`` clears it."""
    sa_id = await _seed_sa(
        admin_db, suffix="vrot", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_vrot"
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id
    assert agent_id is not None

    # The real credential writers (the ones the agent key routes call).
    async with admin_db.session() as session:
        assert await AgentCredentialRepository.clear_api_key_hash(session, agent_id)
        if regenerate:
            await AgentCredentialRepository.set_api_key_hash(
                session, agent_id, api_key_hash=_digest("ak_t8m_vrot_new"), created_by=_OWNER
            )
        await session.commit()

    result = await svc.verify(acknowledge=True)

    assert result.digest_mismatch_count == 0
    assert result.passed is True
    assert result.acknowledged is True
    assert _findings(result, "digest_mismatch") == []
    superseded = _findings(result, "successor_digest_superseded")
    assert [
        (f["service_account_id"], f["successor_agent_id"], f["reason"]) for f in superseded
    ] == [(sa_id, agent_id, "successor_key_rotated")]
    assert superseded[0]["informational"] is True
    assert "--sweep-migrated" in superseded[0]["actionable_step"]
    assert result.superseded_digest_count == 1
    assert result.finding_count == 0
    acks = await _rows(
        admin_db,
        "SELECT digest_mismatch_count, report_finding_count FROM service_account_migration_acks",
        {},
    )
    assert [(a.digest_mismatch_count, a.report_finding_count) for a in acks] == [(0, 0)]

    await svc.sweep(ignore_age_gate=True)
    after_sweep = await svc.verify()

    assert after_sweep.passed is True
    assert after_sweep.superseded_digest_count == 0
    assert _findings(after_sweep, "successor_digest_superseded") == []


async def test_verify_excludes_archived_successor_from_digest_mismatches(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """#1416: an archived successor is terminal — a digest it no longer holds
    is superseded, not drift, even when the change carried no rotation stamp."""
    sa_id = await _seed_sa(
        admin_db, suffix="varch", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_varch"
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id

    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE agents SET status = 'archived' WHERE id = :id"), {"id": agent_id}
        )
        await session.execute(
            text("UPDATE agent_credentials SET api_key_hash = NULL WHERE agent_id = :id"),
            {"id": agent_id},
        )
        await session.commit()

    result = await svc.verify()

    assert result.digest_mismatch_count == 0
    assert result.passed is True
    assert [
        (f["service_account_id"], f["reason"])
        for f in _findings(result, "successor_digest_superseded")
    ] == [(sa_id, "successor_archived")]


@pytest.mark.parametrize(
    "tamper",
    [
        # Digest changed by a writer that bypassed the credential repository
        # (no rotated_at stamp).
        "UPDATE agent_credentials SET api_key_hash = :other WHERE agent_id = :id",
        # A rotation stamp that predates the migration is not a post-stamp rotation.
        "UPDATE agent_credentials SET api_key_hash = :other, rotated_at = :before"
        " WHERE agent_id = :id",
        # Successor credential row gone.
        "DELETE FROM agent_credentials WHERE agent_id = :id",
        # Successor agent row gone (cascades its credential row).
        "DELETE FROM agents WHERE id = :id",
    ],
    ids=["unstamped-change", "rotated-before-stamp", "credential-missing", "successor-missing"],
)
async def test_verify_still_counts_genuine_digest_drift_and_names_the_pair(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None, tamper: str
) -> None:
    """#1416 must stay fail-closed: drift that is not a legitimate post-stamp
    rotation or archive is counted, refuses the ack, and names the SA and
    successor ids."""
    sa_id = await _seed_sa(admin_db, suffix="vdrift", api_key_plaintext="sak_t8m_vdrift")
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id
    _, stamp = await _stamp_of(admin_db, sa_id)
    # Raw-SQL read: a datetime on Postgres, the stored ISO string on SQLite.
    migrated_at = stamp if isinstance(stamp, dt.datetime) else dt.datetime.fromisoformat(str(stamp))

    async with admin_db.session() as session:
        await session.execute(
            text(tamper),
            {
                "id": agent_id,
                "other": _digest("sak_t8m_vdrift_other"),
                "before": migrated_at - dt.timedelta(hours=1),
            },
        )
        await session.commit()

    result = await svc.verify(acknowledge=True)

    assert result.digest_mismatch_count == 1
    assert result.passed is False
    assert result.acknowledged is False
    assert result.only_sweep_healable_failures is False
    mismatches = _findings(result, "digest_mismatch")
    assert [(f["service_account_id"], f["successor_agent_id"]) for f in mismatches] == [
        (sa_id, agent_id)
    ]
    assert _findings(result, "successor_digest_superseded") == []
    acks = await _rows(admin_db, "SELECT id FROM service_account_migration_acks", {})
    assert acks == []


# ------------------------------------------------- review follow-ups (PR #1386)


async def test_migration_derives_state_from_in_transaction_reread_not_list_snapshot(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """H1: a key rotation and a disable landing between ``run()``'s list and
    the per-SA transaction are reflected in the successor — status and digest
    come from the locked re-read, never the stale snapshot."""
    old_plaintext = "sak_t8m_stale_snapshot"
    sa_id = await _seed_sa(
        admin_db, suffix="snap", scopes=("toolkit:read",), api_key_plaintext=old_plaintext
    )
    async with admin_db.session() as session:
        rows = await ServiceAccountMigrationRepository.list_service_accounts(session)
    stale = next(r for r in rows if r.id == sa_id)
    assert stale.status == "active"

    # The SA write surface is gone (theme-8 Phase 2); a concurrent rotation +
    # disable is simulated at the row level.
    new_plaintext = "sak_t8m_stale_snapshot_rotated"
    async with admin_db.session() as session:
        await session.execute(
            text(
                "UPDATE service_account_credentials SET api_key_hash = :h"
                " WHERE service_account_id = :id"
            ),
            {"h": _digest(new_plaintext), "id": sa_id},
        )
        await session.execute(
            text("UPDATE service_accounts SET status = 'disabled' WHERE id = :id"),
            {"id": sa_id},
        )
        await session.commit()

    outcome = await ServiceAccountMigrationService(integration_context)._migrate_one(stale)

    assert outcome.outcome == "migrated-disabled"
    agent_id = outcome.successor_agent_id
    agents = await _rows(admin_db, "SELECT status FROM agents WHERE id = :id", {"id": agent_id})
    assert [r.status for r in agents] == ["disabled"]
    digests = await _rows(
        admin_db,
        "SELECT api_key_hash FROM agent_credentials WHERE agent_id = :id",
        {"id": agent_id},
    )
    assert [r.api_key_hash for r in digests] == [_digest(new_plaintext)]
    assert _digest(old_plaintext) != _digest(new_plaintext)


@pytest.fixture()
async def rule_credential(control_db: DatabaseSession) -> AsyncGenerator[str, None]:
    """A control-DB credential the inline-rule tests hang rules off."""
    cred_id = "cred_t8m_rules"

    async def _cleanup() -> None:
        async with control_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_permission_rules WHERE credential_id = :c"),
                {"c": cred_id},
            )
            await session.execute(text("DELETE FROM credentials WHERE id = :c"), {"c": cred_id})
            await session.commit()

    await _cleanup()
    async with control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name="t8m inline-rule credential",
                api_vendor="stripe",
                api_name="payments",
                api_version="v1",
            )
        )
        await session.commit()
    yield cred_id
    await _cleanup()


async def _seed_inline_rules(control_db: DatabaseSession, actor_id: str, cred_id: str) -> None:
    async with control_db.session() as session:
        session.add_all(
            [
                AgentPermissionRule(
                    agent_id=actor_id,
                    credential_id=cred_id,
                    effect="allow",
                    methods=["GET"],
                    path="/v1/charges.*",
                    comment="t8m read charges",
                    sequence=0,
                    created_by=_OWNER,
                ),
                AgentPermissionRule(
                    agent_id=actor_id,
                    credential_id=cred_id,
                    effect="deny",
                    methods=["DELETE"],
                    path=".*",
                    sequence=1,
                    created_by=_OWNER,
                ),
            ]
        )
        await session.commit()


async def _inline_rules(
    control_db: DatabaseSession, actor_id: str
) -> list[tuple[str, str, int, str | None]]:
    async with control_db.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT credential_id, effect, sequence, path FROM agent_permission_rules"
                    " WHERE agent_id = :id ORDER BY credential_id, sequence"
                ),
                {"id": actor_id},
            )
        ).all()
    return [(r.credential_id, r.effect, r.sequence, r.path) for r in rows]


async def test_inline_permission_rules_are_copied_idempotently_verified_and_swept(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """H2: control-DB ``agent_permission_rules`` keyed ``(sva_, credential)``
    are copied onto the successor, re-runs never duplicate them, verify
    criterion 6 checks per-binding parity, and the sweep drops the sva_ rows."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="rules",
        api_key_plaintext="sak_t8m_rules",
        credential_ids=(rule_credential,),
    )
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    source = await _inline_rules(control_db, sa_id)
    svc = ServiceAccountMigrationService(integration_context)

    first = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert first.outcome == "migrated"
    assert first.permission_rule_count == 2
    agent_id = first.successor_agent_id
    assert agent_id is not None
    assert await _inline_rules(control_db, agent_id) == source
    assert await _inline_rules(control_db, sa_id) == source  # originals kept (N1)

    # Idempotent re-run (the already_migrated heal path): nothing new.
    second = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert second.outcome == "already_migrated"
    assert second.permission_rule_count == 0
    assert await _inline_rules(control_db, agent_id) == source

    verified = await svc.verify()
    assert verified.inline_rule_mismatch_count == 0

    # Criterion 6 catches a successor binding that drifted from its source.
    async with control_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :id AND sequence = 1"),
            {"id": agent_id},
        )
        await session.commit()
    drifted = await svc.verify()
    assert drifted.inline_rule_mismatch_count == 1
    assert drifted.passed is False
    # A partially-edited successor binding is never merged into by a re-run.
    third = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert third.permission_rule_count == 0
    assert len(await _inline_rules(control_db, agent_id)) == 1

    # A lost control step (whole binding missing on the successor) is healed.
    async with control_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :id"), {"id": agent_id}
        )
        await session.commit()
    healed = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert healed.outcome == "already_migrated"
    assert healed.permission_rule_count == 2
    assert await _inline_rules(control_db, agent_id) == source

    swept = await svc.sweep(ignore_age_gate=True)
    assert swept.swept == [sa_id]
    assert swept.permission_rules_deleted == 2
    assert await _inline_rules(control_db, sa_id) == []
    assert await _inline_rules(control_db, agent_id) == source
    after_sweep = await svc.verify()
    assert after_sweep.inline_rule_mismatch_count == 0

    # A repeated sweep finds nothing left to delete.
    again = await svc.sweep(ignore_age_gate=True)
    assert again.permission_rules_deleted == 0


async def test_sweep_ensures_the_successor_twin_before_deleting_sva_rules(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 x L1: if the migration's control step failed and was never healed,
    the sweep copies the rules to the successor before dropping the sva_
    originals — the rules are never lost."""
    sa_id = await _seed_sa(admin_db, suffix="rulesheal", credential_ids=(rule_credential,))
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    source = await _inline_rules(control_db, sa_id)

    async def _failing_copy(session: Any, *, service_account_id: str, agent_id: str) -> int:
        raise RuntimeError("simulated control-DB outage")

    original = ServiceAccountMigrationRepository.copy_permission_rules
    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", _failing_copy)
    svc = ServiceAccountMigrationService(integration_context)
    outcome = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert outcome.reason == "control_sync_error:RuntimeError"
    agent_id = outcome.successor_agent_id
    assert agent_id is not None
    assert await _inline_rules(control_db, agent_id) == []

    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", original)
    swept = await svc.sweep(ignore_age_gate=True)
    assert swept.permission_rules_deleted == 2
    assert await _inline_rules(control_db, sa_id) == []
    assert await _inline_rules(control_db, agent_id) == source


async def test_sweep_deletes_inline_rules_left_by_an_interrupted_sweep(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """H2: the control pass also reaches stamped rows whose admin side a
    previous sweep already finished (crash between the two DB steps), and
    skip-stamped rows (no successor) lose their sva_ rules too."""
    sa_id = await _seed_sa(admin_db, suffix="rulesskip", status="pending")
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    first = await svc.sweep(ignore_age_gate=True)
    assert first.swept == [sa_id]
    assert first.permission_rules_deleted == 2

    # Simulate the lost control step: rules reappear, admin side is done.
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    second = await svc.sweep(ignore_age_gate=True)
    assert second.swept == []  # nothing left on the admin side
    assert second.permission_rules_deleted == 2
    assert await _inline_rules(control_db, sa_id) == []


async def test_sweep_revokes_sa_sessions_minted_during_the_window(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """M1: SA sessions that appear after the stamp (pre-Phase-2 a
    client-credentials login minted them; the grant is gone now, but a
    pre-upgrade session can still be live) are revoked by the sweep in its
    transaction — ``--sweep-migrated`` is the kill lever."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="ccgrant",
        scopes=("toolkit:read",),
        api_key_plaintext="sak_t8m_ccgrant",
    )
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    now = dt.datetime.now(dt.UTC)
    async with admin_db.session() as session:
        session.add(
            AccessToken(
                id="at_t8m_ccgrant_window",
                token_hash=_digest("at_t8m_ccgrant_window"),
                actor_id=sa_id,
                actor_type="service_account",
                scopes=["toolkit:read"],
                token_family_id="tf_t8m_ccgrant_window",
                expires_at=now + dt.timedelta(hours=1),
                created_by=_OWNER,
            )
        )
        session.add(
            RefreshToken(
                id="rt_t8m_ccgrant_window",
                token_hash=_digest("rt_t8m_ccgrant_window"),
                actor_id=sa_id,
                actor_type="service_account",
                scopes=["toolkit:read"],
                token_family_id="tf_t8m_ccgrant_window",
                expires_at=now + dt.timedelta(days=7),
                created_by=_OWNER,
            )
        )
        await session.commit()
    live = await svc.verify()
    assert live.unrevoked_token_count == 2  # the window access + refresh pair
    assert live.only_sweep_healable_failures  # the refusal hint names the sweep

    swept = await svc.sweep(ignore_age_gate=True)
    assert swept.swept == [sa_id]
    assert swept.access_tokens_revoked == 1
    assert swept.refresh_tokens_revoked == 1
    row_line, summary = swept.report_lines(ignore_age_gate=True)
    assert row_line["service_account_id"] == sa_id
    assert (row_line["access_tokens_revoked"], row_line["refresh_tokens_revoked"]) == (1, 1)
    assert summary["category"] == "sweep_summary" and summary["swept"] == 1
    after = await svc.verify()
    assert after.unrevoked_token_count == 0
    revokes = await _rows(
        admin_db,
        "SELECT id FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND reason = 'theme8_sa_migration_sweep_token_revoke' AND target_id = :id",
        {"id": sa_id},
    )
    assert len(revokes) == 1


async def test_control_db_failure_is_a_row_outcome_not_a_run_abort(
    integration_context: Context,
    admin_db: DatabaseSession,
    seed_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1: a control-DB error for one SA is reported on that row; the run
    continues, and the next run heals via the already_migrated path."""
    poisoned = await _seed_sa(admin_db, suffix="ctl_a", api_key_plaintext="sak_t8m_ctl_a")
    healthy = await _seed_sa(admin_db, suffix="ctl_b", api_key_plaintext="sak_t8m_ctl_b")

    original = ServiceAccountMigrationRepository.copy_permission_rules

    async def _poisoned_copy(session: Any, *, service_account_id: str, agent_id: str) -> int:
        if service_account_id == poisoned:
            raise RuntimeError("simulated control-DB outage")
        return await original(session, service_account_id=service_account_id, agent_id=agent_id)

    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", _poisoned_copy)
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}

    assert outcomes[poisoned].outcome == "failed"
    assert outcomes[poisoned].reason == "control_sync_error:RuntimeError"
    assert outcomes[poisoned].successor_agent_id is not None  # admin side committed
    stamp, _ = await _stamp_of(admin_db, poisoned)
    assert stamp == outcomes[poisoned].successor_agent_id
    assert outcomes[healthy].outcome == "migrated"

    # Still failing on the already_migrated path: reported, never raised.
    rerun_failing = {o.service_account_id: o for o in await svc.run()}
    assert rerun_failing[poisoned].outcome == "failed"
    assert rerun_failing[poisoned].reason == "control_sync_error:RuntimeError"

    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", original)
    healed = {o.service_account_id: o for o in await svc.run()}
    assert healed[poisoned].outcome == "already_migrated"
    assert healed[poisoned].reason is None


# ------------------------------------------------- admin-level grant reporting


async def test_admin_level_grant_is_carried_over_and_reported_not_stripped(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """An SA holding ``org:admin`` keeps it on its successor (the grant copy is
    unchanged), and the carry-over is reported: preview + run report line,
    one WARNING per admin-level grant, the scope names in the GRANT audit
    row, and an informational ``verify`` finding that does not fail it."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="admin",
        scopes=("capabilities:execute", "org:admin"),
        api_key_plaintext="sak_t8m_admin",
    )
    plain_sa = await _seed_sa(
        admin_db, suffix="plain", scopes=("capabilities:execute",), api_key_plaintext="sak_t8m_pl"
    )
    svc = ServiceAccountMigrationService(integration_context)
    expected_admin = ({"scope": "org:admin", "original_granted_by": _OWNER},)

    preview = {o.service_account_id: o for o in await svc.run(diff_only=True)}
    assert preview[sa_id].admin_level_scopes == expected_admin
    assert preview[sa_id].copied_scopes == ("capabilities:execute", "org:admin")
    assert preview[plain_sa].admin_level_scopes == ()

    with structlog.testing.capture_logs() as logs:
        outcomes = {o.service_account_id: o for o in await svc.run()}

    outcome = outcomes[sa_id]
    assert outcome.outcome == "migrated"
    assert outcome.stored_scope_count == 2
    assert outcome.copied_scopes == ("capabilities:execute", "org:admin")
    assert outcome.admin_level_scopes == expected_admin
    assert outcomes[plain_sa].admin_level_scopes == ()
    agent_id = outcome.successor_agent_id
    assert agent_id is not None

    # Nothing stripped: the successor holds org:admin exactly as the SA did.
    grants = await _rows(
        admin_db,
        "SELECT scope FROM actor_scope_grants WHERE actor_id = :id AND actor_type = 'agent'",
        {"id": agent_id},
    )
    assert {r.scope for r in grants} == {"capabilities:execute", "org:admin"}

    warnings = [
        log for log in logs if log["event"] == "service_account_migration_admin_scope_copied"
    ]
    assert len(warnings) == 1
    (warning,) = warnings
    assert warning["log_level"] == "warning"
    assert warning["service_account_id"] == sa_id
    assert warning["successor_agent_id"] == agent_id
    assert warning["owner_id"] == _OWNER
    assert warning["scope"] == "org:admin"
    assert warning["original_granted_by"] == _OWNER
    assert not any(v == "sak_t8m_admin" for log in logs for v in log.values())

    (audit,) = await _rows(
        admin_db,
        "SELECT after FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND action = 'grant' AND target_id = :id",
        {"id": agent_id},
    )
    after = json.loads(audit.after) if isinstance(audit.after, str) else audit.after
    assert after == {
        "copied_scope_count": 2,
        "copied_scopes": ["capabilities:execute", "org:admin"],
        "admin_level_scopes": ["org:admin"],
    }

    result = await svc.verify()
    assert result.passed, result.findings[0]
    assert result.successor_admin_scope_count == 1
    (finding,) = [f for f in result.findings if f["category"] == "successor_admin_scope"]
    assert finding["agent_id"] == agent_id
    assert finding["agent_name"] == f"service-account:{sa_id}"
    assert finding["owner_id"] == _OWNER
    assert finding["scope"] == "org:admin"
    assert finding["informational"] is True
    assert result.findings[0]["successor_admin_scope_count"] == 1

    # Informational only: acknowledgement is still granted on a passing verify,
    # and the review lines do not count as findings on the gate row.
    acked = await svc.verify(acknowledge=True)
    assert acked.acknowledged
    (ack,) = await _rows(
        admin_db, "SELECT report_finding_count FROM service_account_migration_acks", {}
    )
    assert ack.report_finding_count == 0

    # A scope an operator re-grants themselves is no longer "carried over".
    async with admin_db.session() as session:
        await session.execute(
            text(
                "UPDATE actor_scope_grants SET granted_by = :by"
                " WHERE actor_id = :id AND scope = 'org:admin'"
            ),
            {"by": _OWNER, "id": agent_id},
        )
        await session.commit()
    regranted = await svc.verify()
    assert regranted.successor_admin_scope_count == 0
    assert not [f for f in regranted.findings if f["category"] == "successor_admin_scope"]
