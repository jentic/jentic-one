"""Integration tests for the theme-8 Phase 1 service-account → agent migration.

Runs ``ServiceAccountMigrationService`` against real admin (+ control)
databases on both dialects (``JENTIC_TEST_BACKEND=sqlite`` locally, Postgres
in CI): the copy→revoke→stamp→audit transaction, dispositions (OQ-1),
resolver behaviour (agent arm; no SA fallback since theme-8 Phase 4), the
deferred sweep (W3/N3), and the Phase-4 ``retire()`` pre-drop step
(migrate → verify → sweep, refusing by SA id). Test names lift the plan's
acceptance criteria verbatim where they apply.

The service runs inside the migration runner, before the admin drop migration
(``e2f3a4b5c6d7``), so it runs against the pre-drop schema: the module
restores the pre-drop admin schema (the drop's downgrade raises; the helper
models the pre-upgrade snapshot restore) and re-upgrades at teardown through
the drop's fresh-install path, since the suite leaves the tables empty.
The SA ORM models are gone, so seeding is raw SQL.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
import structlog
from alembic import command
from sqlalchemy import text

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
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
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from tests.integration.conftest import _alembic_config_for
from tests.integration.service_account_schema import restore_pre_sa_drop_admin

pytestmark = pytest.mark.integration

_OWNER = "usr_t8m_owner"


@pytest.fixture(scope="module")
def service_account_tables(integration_config: AppConfig) -> Iterator[None]:
    """Restore the pre-drop SA tables (the drop's downgrade raises), then re-drop."""
    admin_cfg = _alembic_config_for("admin", integration_config.databases.admin)
    restore_pre_sa_drop_admin(integration_config)
    yield
    command.upgrade(admin_cfg, "head")


@pytest.fixture()
async def clean_tables(
    admin_db: DatabaseSession, service_account_tables: None
) -> AsyncGenerator[None, None]:
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
        await session.execute(
            text(
                "INSERT INTO service_accounts"
                " (id, name, owner_id, registered_by, status, created_by)"
                " VALUES (:id, :name, :owner, :owner, :status, :owner)"
            ),
            {"id": sa_id, "name": f"t8m-{suffix}", "owner": _OWNER, "status": status},
        )
        await session.execute(
            text(
                "INSERT INTO service_account_credentials"
                " (id, service_account_id, api_key_hash, client_secret_hash, created_by)"
                " VALUES (:id, :sa_id, :api_key_hash, :client_secret_hash, :owner)"
            ),
            {
                "id": f"sac_t8m_{suffix}",
                "sa_id": sa_id,
                "api_key_hash": _digest(api_key_plaintext) if api_key_plaintext else None,
                "client_secret_hash": client_secret_hash,
                "owner": _OWNER,
            },
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
    family-revoked in the stamp transaction, and the pre-drop retirement
    accepts and sweeps it (no successor to verify)."""
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

    retired = await svc.retire()
    assert retired.action == "retired"
    assert retired.swept == 1
    status = await _rows(
        admin_db, "SELECT status FROM service_accounts WHERE id = :id", {"id": sa_id}
    )
    assert status[0].status == "archived"


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


async def test_converted_jntc_live_key_authenticates_as_successor_with_identical_scopes(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """A retired toolkit key the theme-5 retirement converted into a service
    account keeps working through the migration (until at least 2026-12-01):
    the SA's digest copied onto the successor resolves as that agent."""
    plaintext = "jntc_live_t8m_resolve"
    sa_id = await _seed_sa(
        admin_db,
        suffix="resolve",
        scopes=("capabilities:execute", "toolkit:read"),
        api_key_plaintext=plaintext,
    )
    resolver = ApiKeyResolver(admin_db)
    # Theme-8 Phase 4: no SA fallback — an unmigrated key does not resolve.
    assert await resolver.resolve(plaintext) is None

    outcomes = await ServiceAccountMigrationService(integration_context).run()
    agent_id = {o.service_account_id: o for o in outcomes}[sa_id].successor_agent_id

    after = await resolver.resolve(plaintext)
    assert after is not None
    assert after.sub == agent_id
    assert after.actor_type is ActorType.AGENT
    assert sorted(after.permissions) == ["capabilities:execute", "toolkit:read"]


@pytest.mark.parametrize("migrate", [True, False], ids=["migrated", "unmigrated"])
async def test_sak_key_never_authenticates_migrated_or_not(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None, migrate: bool
) -> None:
    """0.41: ``sak_`` keys stop working. Migrated (the successor holds the
    copied digest) or not, the key is refused; the INFO line names the
    successor when there is one."""
    plaintext = f"sak_t8m_dead_{migrate}"
    sa_id = await _seed_sa(
        admin_db, suffix=f"dead_{migrate}", scopes=("toolkit:read",), api_key_plaintext=plaintext
    )
    agent_id = None
    if migrate:
        outcomes = await ServiceAccountMigrationService(integration_context).run()
        agent_id = {o.service_account_id: o for o in outcomes}[sa_id].successor_agent_id
        held = await _rows(
            admin_db,
            "SELECT api_key_hash FROM agent_credentials WHERE agent_id = :id",
            {"id": agent_id},
        )
        assert [r.api_key_hash for r in held] == [_digest(plaintext)]

    resolver = ApiKeyResolver(admin_db)
    with structlog.testing.capture_logs() as logs:
        identity = await resolver.resolve(plaintext)

    assert identity is None
    refused = [log for log in logs if log["event"] == "retired_service_account_key_refused"]
    assert len(refused) == 1 and refused[0]["log_level"] == "info"
    assert refused[0]["successor_agent_id"] == agent_id


async def test_disabled_sa_successor_created_disabled_and_key_dead_until_agent_enable(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    plaintext = "jntc_live_t8m_disabled"
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
    plaintext = "jntc_live_t8m_killlever"
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

    assert identity is None  # fail closed
    fail_closed = [log for log in logs if log["event"] == "migrated_key_fail_closed"]
    assert len(fail_closed) == 1 and fail_closed[0]["reason"] == "successor_inactive"


async def test_revoking_the_successor_key_fails_closed(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """H1(b): revoking/rotating the successor's key NULLs the agent-side
    digest — a genuine agent-arm miss — and the still-live SA-side digest
    must not resurrect the key (there is no SA fallback)."""
    plaintext = "jntc_live_t8m_revlever"
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
    assert [log for log in logs if log["event"] == "retired_key_unresolved"]


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


# ---------------------------------------------------------------- W3 sweep


async def test_sweep_clears_sa_satellites_and_archives(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    plaintext = "jntc_live_t8m_sweep"  # a converted toolkit key keeps serving
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

    # Theme-8 Phase 4 removed the boot job and its stamp-age gate: the
    # operator-run sweep acts on every stamped row, however fresh.
    swept = await svc.sweep()
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

    # Post-sweep: the agent arm still serves (T-6c).
    resolver = ApiKeyResolver(admin_db)
    identity = await resolver.resolve(plaintext)
    assert identity is not None and identity.sub == agent_id


async def test_sweep_archives_migrated_and_skip_stamp_rows(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    migrated = await _seed_sa(admin_db, suffix="aged", api_key_plaintext="sak_t8m_aged")
    skipped = await _seed_sa(admin_db, suffix="agedskip", status="pending")
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    outcome = await svc.sweep()

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

    first = await svc.sweep()
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
    second = await svc.sweep()
    assert sa_id not in second.swept


async def test_repeated_sweeps_write_exactly_one_archive_audit_row(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """L3: the archive UPDATE's in-transaction re-check (status != 'archived')
    keeps re-sweeps from duplicating the ARCHIVE audit row."""
    sa_id = await _seed_sa(admin_db, suffix="resweep", api_key_plaintext="sak_t8m_resweep")
    svc = ServiceAccountMigrationService(integration_context)
    await svc.run()

    await svc.sweep()
    await svc.sweep()

    archives = await _rows(
        admin_db,
        "SELECT id FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND action = 'archive' AND target_id = :id",
        {"id": sa_id},
    )
    assert len(archives) == 1


# ------------------------------------------------------- W8 stamp guards (M4)


async def test_retire_heals_post_stamp_sva_binding_inserts(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """M4: a fresh ``sva_``-keyed binding written after the stamp (raw-SQL
    writers bypassing the service guards) is copied onto the successor by the
    pre-drop retirement, then swept from the SA side."""
    sa_id = await _seed_sa(
        admin_db, suffix="vbind", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_vbind"
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id

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

    retired = await svc.retire()

    assert retired.action == "retired"
    assert retired.post_stamp_bindings_copied == 1
    assert retired.post_stamp_grants_copied == 0
    bindings = await _rows(
        admin_db,
        "SELECT agent_id FROM agent_credential_bindings WHERE credential_id = 'cred_t8m_late'",
        {},
    )
    assert [b.agent_id for b in bindings] == [agent_id]


# ------------------------------------------------ Phase-4 retire (pre-drop)


async def test_retire_migrates_unstamped_rows_and_sweeps_them(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """An unmigrated active SA and a rejected one: ``retire()`` migrates,
    verifies and sweeps in one call — no operator step, no acknowledgement."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="vpass",
        scopes=("toolkit:read",),
        api_key_plaintext="sak_t8m_vpass",
        with_tokens=True,
    )
    skip_id = await _seed_sa(admin_db, suffix="vpass_skip", status="rejected")
    svc = ServiceAccountMigrationService(integration_context)

    retired = await svc.retire()

    assert (retired.action, retired.migrated, retired.skipped, retired.swept) == (
        "retired",
        1,
        1,
        2,
    )
    for sid in (sa_id, skip_id):
        stamp, _ = await _stamp_of(admin_db, sid)
        assert stamp is not None
    statuses = await _rows(
        admin_db, "SELECT DISTINCT status FROM service_accounts WHERE id LIKE 'sva_t8m_%'", {}
    )
    assert [s.status for s in statuses] == ["archived"]
    async with admin_db.session() as session:
        unstamped = await ServiceAccountMigrationRepository.list_unstamped_ids(session)
    assert unstamped == []


async def test_retire_is_idempotent(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    await _seed_sa(
        admin_db, suffix="videm", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_videm"
    )
    svc = ServiceAccountMigrationService(integration_context)

    first = await svc.retire()
    second = await svc.retire()

    assert (first.migrated, first.swept) == (1, 1)
    assert (second.action, second.migrated, second.already_migrated) == ("retired", 0, 1)
    assert (second.post_stamp_grants_copied, second.post_stamp_bindings_copied) == (0, 0)
    successors = await _rows(
        admin_db, "SELECT id FROM agents WHERE registered_by = 'system:theme8-sa-migration'", {}
    )
    assert len(successors) == 1


async def test_retire_copies_post_stamp_grants_but_never_resurrects_removed_twins(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """For an SA stamped by an earlier run, a successor grant the operator
    removed stays removed (not a refusal), while an SA grant created after
    the stamp is copied (the M4 post-stamp mutation, healed)."""
    sa_id = await _seed_sa(
        admin_db, suffix="vtwin", scopes=("toolkit:read",), api_key_plaintext="sak_t8m_vtwin"
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id

    async with admin_db.session() as session:
        await session.execute(
            text("DELETE FROM actor_scope_grants WHERE actor_id = :id"), {"id": agent_id}
        )
        await session.execute(
            text(
                "INSERT INTO actor_scope_grants"
                " (id, actor_id, actor_type, scope, granted_by, created_by, created_at)"
                " VALUES ('asg_t8m_late', :id, 'service_account', 'capabilities:execute',"
                " :by, :by, :late)"
            ),
            {"id": sa_id, "by": _OWNER, "late": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)},
        )
        await session.commit()

    retired = await svc.retire()

    assert retired.post_stamp_grants_copied == 1
    grants = await _rows(
        admin_db, "SELECT scope FROM actor_scope_grants WHERE actor_id = :id", {"id": agent_id}
    )
    assert [g.scope for g in grants] == ["capabilities:execute"]


@pytest.mark.parametrize("regenerate", [True, False], ids=["rotated", "revoked"])
async def test_retire_accepts_a_successor_key_rotation(
    integration_context: Context,
    admin_db: DatabaseSession,
    seed_owner: None,
    regenerate: bool,
) -> None:
    """#1416: an operator rotating (revoke + regenerate) or just revoking the
    successor's key after the stamp is not drift — the retirement passes and
    sweeps the superseded SA digest."""
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

    retired = await svc.retire()

    assert (retired.action, retired.swept) == ("retired", 1)
    digests = await _rows(
        admin_db,
        "SELECT api_key_hash FROM service_account_credentials WHERE service_account_id = :id",
        {"id": sa_id},
    )
    assert [d.api_key_hash for d in digests] in ([], [None])


async def test_retire_accepts_an_archived_successor(
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

    retired = await svc.retire()

    assert (retired.action, retired.swept) == ("retired", 1)


@pytest.mark.parametrize(
    "tamper",
    [
        # Digest changed without a rotation stamp.
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
async def test_retire_does_not_gate_on_the_successor_key_digest(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None, tamper: str
) -> None:
    """0.41 dropped the API-key digest parity check: ``sak_`` keys stop
    working whatever the successor's credential row holds, so a digest the
    successor no longer carries (however it changed) does not block the
    retirement. The SA-side digest is still swept."""
    sa_id = await _seed_sa(
        admin_db, suffix="vdrift", api_key_plaintext="sak_t8m_vdrift", with_tokens=True
    )
    svc = ServiceAccountMigrationService(integration_context)
    outcomes = {o.service_account_id: o for o in await svc.run()}
    agent_id = outcomes[sa_id].successor_agent_id
    assert agent_id is not None
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

    retired = await svc.retire()

    assert (retired.action, retired.swept) == ("retired", 1)
    rows = await _rows(
        admin_db,
        "SELECT sa.status, c.api_key_hash FROM service_accounts sa"
        " JOIN service_account_credentials c ON c.service_account_id = sa.id"
        " WHERE sa.id = :id",
        {"id": sa_id},
    )
    assert [(r.status, r.api_key_hash) for r in rows] == [("archived", None)]


async def test_retire_without_the_tables_is_a_noop(
    integration_context: Context, integration_config: AppConfig, clean_tables: None
) -> None:
    """Past the drop there is nothing to retire (the runner never calls it
    there, but the step must stay safe to call)."""
    admin_cfg = _alembic_config_for("admin", integration_config.databases.admin)
    await asyncio.to_thread(command.upgrade, admin_cfg, "head")
    try:
        retired = await ServiceAccountMigrationService(integration_context).retire()
    finally:
        await asyncio.to_thread(restore_pre_sa_drop_admin, integration_config)
    assert retired.action == "no_tables"


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


async def test_inline_permission_rules_are_copied_idempotently_and_swept(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """H2: control-DB ``agent_permission_rules`` keyed ``(sva_, credential)``
    are copied onto the successor, re-runs never duplicate them, never merge
    into an operator-edited successor binding and never refill one the
    operator emptied, and the sweep drops the sva_ rows."""
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

    # An operator edits the successor binding (drops one rule).
    async with control_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :id AND sequence = 1"),
            {"id": agent_id},
        )
        await session.commit()
    # A partially-edited successor binding is never merged into by a re-run.
    third = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert third.permission_rule_count == 0
    assert len(await _inline_rules(control_db, agent_id)) == 1

    # The operator empties the successor binding: a re-run leaves it empty.
    async with control_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :id"), {"id": agent_id}
        )
        await session.commit()
    emptied = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert emptied.outcome == "already_migrated"
    assert emptied.permission_rule_count == 0
    assert await _inline_rules(control_db, agent_id) == []

    swept = await svc.sweep()
    assert swept.swept == [sa_id]
    assert swept.permission_rules_deleted == 2
    assert await _inline_rules(control_db, sa_id) == []
    assert await _inline_rules(control_db, agent_id) == []

    # A repeated sweep finds nothing left to delete.
    again = await svc.sweep()
    assert again.permission_rules_deleted == 0


async def test_retire_deletes_orphan_sva_inline_rules_and_keeps_the_successor_twin(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """Rules keyed on an ``sva_`` id no SA row owns (a hand-deleted service
    account) cannot be migrated anywhere: the retirement deletes them after
    the sweep, while a real SA's rules end on its successor only."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="orules",
        api_key_plaintext="sak_t8m_orules",
        credential_ids=(rule_credential,),
    )
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    source = await _inline_rules(control_db, sa_id)
    orphan = "sva_t8m_orphan_rules"
    await _seed_inline_rules(control_db, orphan, rule_credential)

    retired = await ServiceAccountMigrationService(integration_context).retire()

    assert retired.action == "retired"
    assert retired.permission_rules_deleted == 2
    assert retired.orphan_permission_rules_deleted == 2
    assert await _inline_rules(control_db, sa_id) == []
    assert await _inline_rules(control_db, orphan) == []
    successor = await _rows(
        admin_db, "SELECT migrated_to_actor_id FROM service_accounts WHERE id = :id", {"id": sa_id}
    )
    assert await _inline_rules(control_db, successor[0].migrated_to_actor_id) == source


async def test_retire_leaves_an_emptied_successor_binding_empty_and_warns(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """No resurrection: an SA migrated by an earlier run whose successor
    binding the operator emptied of inline rules (deny all) stays empty
    through the retirement — never re-copied from the sva_ originals, never
    a refusal — and the retirement summary carries a WARNING line naming the
    service account, the successor and what was not copied."""
    sa_id = await _seed_sa(
        admin_db,
        suffix="rempty",
        api_key_plaintext="sak_t8m_rempty",
        credential_ids=(rule_credential,),
    )
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    svc = ServiceAccountMigrationService(integration_context)
    agent_id = {o.service_account_id: o for o in await svc.run()}[sa_id].successor_agent_id
    assert agent_id is not None
    async with control_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :id"), {"id": agent_id}
        )
        await session.commit()

    with structlog.testing.capture_logs() as logs:
        retired = await svc.retire()

    assert (retired.action, retired.swept) == ("retired", 1)
    assert await _inline_rules(control_db, agent_id) == []
    assert await _inline_rules(control_db, sa_id) == []
    [line] = retired.warnings
    assert line.startswith(f"{sa_id}: 2 inline permission rule(s) for credential {rule_credential}")
    assert f"NOT copied to successor agent {agent_id}" in line
    [warning] = [log for log in logs if log["event"] == "service_account_retirement_not_copied"]
    assert warning["log_level"] == "warning"
    assert (warning["service_account_id"], warning["successor_agent_id"]) == (sa_id, agent_id)


async def test_control_db_failure_before_the_admin_step_writes_nothing(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 x L1: the rule copy runs before the admin transaction, so a
    control-DB outage leaves the SA unstamped with no successor; the next run
    migrates it afresh and its successor gets every rule."""
    sa_id = await _seed_sa(admin_db, suffix="rulesheal", credential_ids=(rule_credential,))
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    source = await _inline_rules(control_db, sa_id)

    async def _failing_copy(session: Any, **_: Any) -> int:
        raise RuntimeError("simulated control-DB outage")

    original = ServiceAccountMigrationRepository.copy_permission_rules
    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", _failing_copy)
    svc = ServiceAccountMigrationService(integration_context)
    outcome = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert (outcome.outcome, outcome.reason) == ("failed", "control_sync_error:RuntimeError")
    assert outcome.successor_agent_id is None
    assert await _stamp_of(admin_db, sa_id) == (None, None)

    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", original)
    healed = {o.service_account_id: o for o in await svc.run()}[sa_id]
    assert (healed.outcome, healed.permission_rule_count) == ("migrated", 2)
    assert healed.successor_agent_id is not None
    assert await _inline_rules(control_db, healed.successor_agent_id) == source


async def test_retire_copies_the_inline_rules_of_a_post_stamp_binding(
    integration_context: Context,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    seed_owner: None,
    rule_credential: str,
) -> None:
    """A binding the retirement creates on an earlier successor (an SA
    binding added after the stamp) gets that binding's inline rules."""
    sa_id = await _seed_sa(admin_db, suffix="rlate", api_key_plaintext="sak_t8m_rlate")
    svc = ServiceAccountMigrationService(integration_context)
    agent_id = {o.service_account_id: o for o in await svc.run()}[sa_id].successor_agent_id
    assert agent_id is not None
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings"
                " (id, agent_id, credential_id, created_by, created_at)"
                " VALUES ('acb_t8m_rlate', :id, :cred, :by, :late)"
            ),
            {
                "id": sa_id,
                "cred": rule_credential,
                "by": _OWNER,
                "late": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            },
        )
        await session.commit()
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    source = await _inline_rules(control_db, sa_id)

    retired = await svc.retire()

    assert (retired.post_stamp_bindings_copied, retired.post_stamp_permission_rules_copied) == (
        1,
        2,
    )
    assert retired.warnings == []
    assert await _inline_rules(control_db, agent_id) == source
    assert await _inline_rules(control_db, sa_id) == []


async def test_retire_never_recreates_a_removed_successor_grant_or_purged_binding(
    integration_context: Context,
    admin_db: DatabaseSession,
    seed_owner: None,
) -> None:
    """A post-stamp SA grant/binding whose counterpart the successor held and
    lost (audited ``replace_scopes`` removal, audited binding purge) is not
    copied; each becomes a WARNING line, and the retirement proceeds."""
    sa_id = await _seed_sa(admin_db, suffix="vgone", api_key_plaintext="sak_t8m_vgone")
    svc = ServiceAccountMigrationService(integration_context)
    agent_id = {o.service_account_id: o for o in await svc.run()}[sa_id].successor_agent_id
    assert agent_id is not None
    late = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO actor_scope_grants"
                " (id, actor_id, actor_type, scope, granted_by, created_by, created_at)"
                " VALUES ('asg_t8m_vgone', :id, 'service_account', 'capabilities:execute',"
                " :by, :by, :late)"
            ),
            {"id": sa_id, "by": _OWNER, "late": late},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings"
                " (id, agent_id, credential_id, created_by, created_at)"
                " VALUES ('acb_t8m_vgone', :id, 'cred_t8m_vgone', :by, :late)"
            ),
            {"id": sa_id, "by": _OWNER, "late": late},
        )
        # The successor held both on its own, and the operator removed them.
        for action, target_type, target_id, parent, before, after, reason in (
            (
                "grant",
                "agent",
                agent_id,
                None,
                {"scopes": ["capabilities:execute"]},
                {"scopes": []},
                "replace_scopes",
            ),
            (
                "revoke",
                "credential_binding",
                "cred_t8m_vgone",
                agent_id,
                None,
                None,
                "purge_credential_binding",
            ),
        ):
            session.add(
                AuditEntry(
                    actor_type="user",
                    actor_id="migrate-service-accounts",  # swept by clean_tables
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    target_parent_id=parent,
                    before=before,
                    after=after,
                    reason=reason,
                )
            )
        await session.commit()

    retired = await svc.retire()

    assert (retired.action, retired.swept) == ("retired", 1)
    assert (retired.post_stamp_grants_copied, retired.post_stamp_bindings_copied) == (0, 0)
    assert (
        await _rows(
            admin_db, "SELECT scope FROM actor_scope_grants WHERE actor_id = :id", {"id": agent_id}
        )
        == []
    )
    assert (
        await _rows(
            admin_db,
            "SELECT id FROM agent_credential_bindings WHERE agent_id = :id",
            {"id": agent_id},
        )
        == []
    )
    assert len(retired.warnings) == 2
    assert any("scope grant 'capabilities:execute'" in w for w in retired.warnings)
    assert any("credential binding cred_t8m_vgone" in w for w in retired.warnings)
    assert all(w.startswith(f"{sa_id}: ") for w in retired.warnings)


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
    # The rules pre-copied for the successor a skip never creates are gone.
    async with control_db.session() as session:
        holders = (
            await session.execute(
                text(
                    "SELECT DISTINCT agent_id FROM agent_permission_rules WHERE credential_id = :c"
                ),
                {"c": rule_credential},
            )
        ).all()
    assert [h.agent_id for h in holders] == [sa_id]

    first = await svc.sweep()
    assert first.swept == [sa_id]
    assert first.permission_rules_deleted == 2

    # Simulate the lost control step: rules reappear, admin side is done.
    await _seed_inline_rules(control_db, sa_id, rule_credential)
    second = await svc.sweep()
    assert second.swept == []  # nothing left on the admin side
    assert second.permission_rules_deleted == 2
    assert await _inline_rules(control_db, sa_id) == []


async def test_sweep_revokes_sa_sessions_minted_during_the_window(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """M1: SA sessions that appear after the stamp (pre-Phase-2 a
    client-credentials login minted them; the grant is gone now, but a
    pre-upgrade session can still be live) are revoked by the sweep in its
    transaction (the pre-drop retirement runs it)."""
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

    swept = await svc.sweep()
    assert swept.swept == [sa_id]
    assert swept.access_tokens_revoked == 1
    assert swept.refresh_tokens_revoked == 1
    for table in ("access_tokens", "refresh_tokens"):
        live = await _rows(
            admin_db,
            f"SELECT id FROM {table} WHERE actor_id = :id AND revoked_at IS NULL",
            {"id": sa_id},
        )
        assert live == [], table
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
    """L1: a control-DB error for one SA is reported on that row (nothing is
    written for it); the run continues, and the next run migrates it."""
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
    assert outcomes[poisoned].successor_agent_id is None
    assert await _stamp_of(admin_db, poisoned) == (None, None)
    assert outcomes[healthy].outcome == "migrated"

    # Still failing: reported, never raised.
    rerun_failing = {o.service_account_id: o for o in await svc.run()}
    assert rerun_failing[poisoned].outcome == "failed"
    assert rerun_failing[poisoned].reason == "control_sync_error:RuntimeError"

    monkeypatch.setattr(ServiceAccountMigrationRepository, "copy_permission_rules", original)
    healed = {o.service_account_id: o for o in await svc.run()}
    assert healed[poisoned].outcome == "migrated"
    assert healed[poisoned].reason is None
    assert healed[healthy].outcome == "already_migrated"


# ------------------------------------------------- admin-level grant reporting


async def test_admin_level_grant_is_carried_over_and_reported_not_stripped(
    integration_context: Context, admin_db: DatabaseSession, seed_owner: None
) -> None:
    """An SA holding ``org:admin`` keeps it on its successor (the grant copy is
    unchanged), and the carry-over is reported: the run report line, one
    WARNING per admin-level grant, the scope names in the GRANT audit row —
    and it never blocks the pre-drop retirement."""
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

    retired = await svc.retire()
    assert (retired.action, retired.swept) == ("retired", 2)
    grants_after = await _rows(
        admin_db,
        "SELECT scope FROM actor_scope_grants WHERE actor_id = :id",
        {"id": agent_id},
    )
    assert {r.scope for r in grants_after} == {"capabilities:execute", "org:admin"}
