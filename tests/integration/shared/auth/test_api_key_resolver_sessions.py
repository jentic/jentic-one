"""Integration tests: ``ApiKeyResolver`` runs one autocommit query per resolve (#1703, #1944).

A broker call that misses the token cache resolves its key against the admin
DB. The credential row, owner liveness, and the agent's permission grants are
read in a single SQL statement on an autocommit read session, so each resolve
checks out one admin connection and executes one statement without
BEGIN/ROLLBACK — whatever the outcome (active with 0..N grants, inactive agent,
digest miss, retired prefix).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest
import structlog.testing
from sqlalchemy import event, text

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from tests.harness.db_round_trips import trace_round_trips

pytestmark = pytest.mark.integration

_OWNER = "usr_akrs_owner"
_AGENT = "agnt_akrs_agent"


@contextmanager
def _count_checkouts(db: DatabaseSession) -> Iterator[list[int]]:
    """Count connection-pool checkouts on ``db``'s engine while the block runs."""
    counter = [0]

    def _on_checkout(*_args: Any) -> None:
        counter[0] += 1

    pool = db.engine.sync_engine.pool
    event.listen(pool, "checkout", _on_checkout)
    try:
        yield counter
    finally:
        event.remove(pool, "checkout", _on_checkout)


@pytest.fixture()
async def clean_tables(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove the admin rows this module seeds, before and after."""

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM actor_permission_grants WHERE actor_id = :id"), {"id": _AGENT}
            )
            await session.execute(
                text("DELETE FROM agent_credentials WHERE agent_id = :id"), {"id": _AGENT}
            )
            await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": _AGENT})
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": _OWNER})
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _seed_agent(
    admin_db: DatabaseSession,
    *,
    plaintext: str,
    status: str,
    owned: bool = True,
    permissions: Sequence[str] = (BROKER_EXECUTE_PERMISSION,),
) -> None:
    """An agent holding ``plaintext``'s digest and the given ``permissions``."""
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 'akrs-owner@test.local', 'Ada', 'K') ON CONFLICT DO NOTHING"
            ),
            {"id": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, 'akrs-bot', :owner, 'system:test', :status, 'system:test')"
            ),
            {"id": _AGENT, "owner": _OWNER if owned else None, "status": status},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by) "
                "VALUES (:id, :agent_id, :hash, 'system:test')"
            ),
            {
                "id": f"agc_{_AGENT}",
                "agent_id": _AGENT,
                "hash": hashlib.sha256(plaintext.encode()).hexdigest(),
            },
        )
        for idx, permission in enumerate(permissions):
            await session.execute(
                text(
                    "INSERT INTO actor_permission_grants (id, actor_id, actor_type, permission, "
                    "created_by) VALUES (:id, :actor_id, 'agent', :permission, 'system:test')"
                ),
                {"id": f"apg_{_AGENT}_{idx}", "actor_id": _AGENT, "permission": permission},
            )
        await session.commit()


@pytest.mark.parametrize("prefix", ["jak_", "jntc_live_"])
async def test_resolve_active_agent_key_uses_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None, prefix: str
) -> None:
    """An active agent's key resolves with its grants in one autocommit statement."""
    plaintext = f"{prefix}{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status="active")

    with _count_checkouts(admin_db) as checkouts, trace_round_trips(admin_db) as trace:
        identity = await ApiKeyResolver(admin_db).resolve(plaintext)

    assert checkouts[0] == 1
    assert len(trace.statements) == 1
    trace.assert_no_begin_or_rollback()
    assert identity is not None
    assert identity.sub == _AGENT
    assert identity.actor_type is ActorType.AGENT
    assert identity.actor_name == "akrs-bot"
    assert identity.parent_actor_id == _OWNER
    assert identity.permissions == [BROKER_EXECUTE_PERMISSION]
    assert identity.active is True


@pytest.mark.parametrize(
    "permissions",
    [
        (),
        (BROKER_EXECUTE_PERMISSION, "capabilities:read", "toolkits:read"),
    ],
    ids=["zero-grants", "multiple-grants"],
)
async def test_resolve_active_agent_key_with_zero_or_multiple_grants_in_one_statement(
    admin_db: DatabaseSession, clean_tables: None, permissions: tuple[str, ...]
) -> None:
    """Both zero-grant and multi-grant active agents resolve in one autocommit statement (#1944)."""
    plaintext = f"jak_{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status="active", permissions=permissions)

    with _count_checkouts(admin_db) as checkouts, trace_round_trips(admin_db) as trace:
        identity = await ApiKeyResolver(admin_db).resolve(plaintext)

    assert checkouts[0] == 1
    assert len(trace.statements) == 1
    trace.assert_no_begin_or_rollback()
    assert identity is not None
    assert identity.sub == _AGENT
    assert sorted(identity.permissions) == sorted(permissions)


@pytest.mark.parametrize("status", ["disabled", "pending", "rejected", "archived"])
async def test_resolve_inactive_agent_key_rejected_with_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None, status: str
) -> None:
    """A key whose agent is not active never resolves, and still costs one statement."""
    plaintext = f"jak_{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status=status)

    with _count_checkouts(admin_db) as checkouts, trace_round_trips(admin_db) as trace:
        identity = await ApiKeyResolver(admin_db).resolve(plaintext)

    assert identity is None
    assert checkouts[0] == 1
    assert len(trace.statements) == 1
    trace.assert_no_begin_or_rollback()


async def test_resolve_unknown_key_rejected_with_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """A digest no agent holds resolves to ``None`` in one autocommit statement."""
    with _count_checkouts(admin_db) as checkouts, trace_round_trips(admin_db) as trace:
        identity = await ApiKeyResolver(admin_db).resolve(f"jak_{secrets.token_hex(16)}")

    assert identity is None
    assert checkouts[0] == 1
    assert len(trace.statements) == 1
    trace.assert_no_begin_or_rollback()


async def _set_owner_active(admin_db: DatabaseSession, *, active: bool) -> None:
    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE users SET active = :active WHERE id = :id"),
            {"active": active, "id": _OWNER},
        )
        await session.commit()


@pytest.mark.parametrize("prefix", ["jak_", "jntc_live_"])
async def test_resolve_key_follows_owner_status_with_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None, prefix: str
) -> None:
    """An active agent's key stops resolving while its owner is inactive, at
    the same one-connection cost, and resolves again once the owner is
    re-enabled. The agent's own status is never changed."""
    plaintext = f"{prefix}{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status="active")
    resolver = ApiKeyResolver(admin_db)

    await _set_owner_active(admin_db, active=False)
    with structlog.testing.capture_logs() as logs, _count_checkouts(admin_db) as checkouts:
        identity = await resolver.resolve(plaintext)

    assert identity is None
    assert checkouts[0] == 1
    owner_inactive = [log for log in logs if log["event"] == "agent_owner_inactive"]
    assert len(owner_inactive) == 1
    assert owner_inactive[0]["log_level"] == "info"
    assert owner_inactive[0]["agent_id"] == _AGENT
    assert owner_inactive[0]["owner_id"] == _OWNER
    # The owner refusal is not reported as an inactive successor.
    assert not [log for log in logs if log["event"] == "migrated_key_fail_closed"]

    await _set_owner_active(admin_db, active=True)
    identity = await resolver.resolve(plaintext)
    assert identity is not None and identity.sub == _AGENT and identity.active is True

    async with admin_db.session() as session:
        status = (
            await session.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": _AGENT})
        ).scalar_one()
    assert status == "active"


async def test_resolve_ownerless_agent_key_is_unaffected(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """An agent with no owner authenticates on its own status alone."""
    plaintext = f"jak_{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status="active", owned=False)
    # Another user's status has no bearing on an ownerless agent.
    await _set_owner_active(admin_db, active=False)

    with _count_checkouts(admin_db) as checkouts:
        identity = await ApiKeyResolver(admin_db).resolve(plaintext)

    assert checkouts[0] == 1
    assert identity is not None
    assert identity.sub == _AGENT
    assert identity.parent_actor_id is None
    assert identity.active is True
