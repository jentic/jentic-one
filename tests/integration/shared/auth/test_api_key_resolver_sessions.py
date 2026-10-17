"""Integration tests: ``ApiKeyResolver`` opens one admin session per resolve (#1703).

A broker call that misses the token cache resolves its key against the admin
DB. The credential row and the agent's permission grants are read on a single
pooled connection, so each resolve checks out exactly one admin connection —
whatever the outcome (active, inactive agent, digest miss, retired prefix).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import structlog.testing
from sqlalchemy import event, text

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

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
    admin_db: DatabaseSession, *, plaintext: str, status: str, owned: bool = True
) -> None:
    """An agent holding ``plaintext``'s digest and the broker execute permission."""
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
        await session.execute(
            text(
                "INSERT INTO actor_permission_grants (id, actor_id, actor_type, permission, "
                "created_by) VALUES (:id, :actor_id, 'agent', :permission, 'system:test')"
            ),
            {"id": f"apg_{_AGENT}", "actor_id": _AGENT, "permission": BROKER_EXECUTE_PERMISSION},
        )
        await session.commit()


@pytest.mark.parametrize("prefix", ["jak_", "jntc_live_"])
async def test_resolve_active_agent_key_uses_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None, prefix: str
) -> None:
    """An active agent's key resolves with its grants over one admin connection."""
    plaintext = f"{prefix}{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status="active")

    with _count_checkouts(admin_db) as checkouts:
        identity = await ApiKeyResolver(admin_db).resolve(plaintext)

    assert checkouts[0] == 1
    assert identity is not None
    assert identity.sub == _AGENT
    assert identity.actor_type is ActorType.AGENT
    assert identity.actor_name == "akrs-bot"
    assert identity.parent_actor_id == _OWNER
    assert identity.permissions == [BROKER_EXECUTE_PERMISSION]
    assert identity.active is True


@pytest.mark.parametrize("status", ["disabled", "pending", "rejected", "archived"])
async def test_resolve_inactive_agent_key_rejected_with_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None, status: str
) -> None:
    """A key whose agent is not active never resolves, and still costs one session."""
    plaintext = f"jak_{secrets.token_hex(16)}"
    await _seed_agent(admin_db, plaintext=plaintext, status=status)

    with _count_checkouts(admin_db) as checkouts:
        identity = await ApiKeyResolver(admin_db).resolve(plaintext)

    assert identity is None
    assert checkouts[0] == 1


async def test_resolve_unknown_key_rejected_with_one_admin_session(
    admin_db: DatabaseSession, clean_tables: None
) -> None:
    """A digest no agent holds resolves to ``None`` over one admin connection."""
    with _count_checkouts(admin_db) as checkouts:
        identity = await ApiKeyResolver(admin_db).resolve(f"jak_{secrets.token_hex(16)}")

    assert identity is None
    assert checkouts[0] == 1


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
    assert owner_inactive[0]["log_level"] == "warning"
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
