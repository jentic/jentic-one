"""Scenario tests for the theme-8 Phase-4 service-account retirement + drop.

Two layers, against the real integration databases (Postgres in CI, SQLite via
``JENTIC_TEST_BACKEND=sqlite``), restoring admin below the drop and
re-upgrading:

- **The runner** (``python -m jentic_one.migrations.run``, driven through
  ``main()``): on a full upgrade it brings admin to ``d1e2f3a4b5c6``, migrates
  + verifies + sweeps every remaining service account, and only then applies
  the drop. Pinned end to end: an unmigrated SA is migrated and dropped, its
  ``sak_`` key is refused while a converted ``jntc_live_`` key resolves to the
  successor, and the runner prints the SA → successor WARNING summary; a failed
  migration refuses with the SA ids, drops nothing, and a re-run after the fix
  succeeds; orphan
  ``sva_`` rows are cleaned; an empty install and a repeated run are no-ops;
  post-stamp SA rows are healed onto the successor.
- **The drop revision's own gate** (``e2f3a4b5c6d7``, plain Alembic — the path
  a targeted/partial upgrade takes, skipping the runner's retirement): refuses
  any unfinished row by id, sweeps the retired scope strings, and the
  irreversible downgrade (it raises; tests reach the pre-drop schema through
  ``tests/integration/service_account_schema.py``, which models the
  pre-upgrade snapshot restore).

The SQLite-only unit twin of the gate is
``tests/unit/test_migration_theme8_drop_service_accounts.py``; the service-level
retirement matrix lives in ``tests/integration/control/test_service_account_migration.py``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.migrations import run as run_mod
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from tests.integration.conftest import _TEST_ENCRYPTION_KEY, _alembic_config_for
from tests.integration.service_account_schema import restore_pre_sa_drop_admin

pytestmark = pytest.mark.integration

_ADMIN_PRE_DROP = "d1e2f3a4b5c6"  # pragma: allowlist secret
_ADMIN_DROP = "e2f3a4b5c6d7"  # pragma: allowlist secret
_SA_TABLES = (
    "service_accounts",
    "service_account_credentials",
    "service_account_migration_acks",
)

_OWNER = "usr_p4test_owner"
_AGENT = "agnt_p4test_succ"
_SA = "sva_p4test_1"
_CRED = "cred_p4test_rules"
_STAMPED_AT = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
_SUCCESSOR_REGISTRAR = "system:theme8-sa-migration"


def _digest(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("admin", integration_config.databases.admin)


async def _table_names(db: DatabaseSession) -> set[str]:
    async with db.session() as session:
        conn = await session.connection()
        return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


async def _exec(db: DatabaseSession, sql: str, params: dict[str, object] | None = None) -> None:
    async with db.session() as session:
        await session.execute(text(sql), params or {})
        await session.commit()


async def _scalar(db: DatabaseSession, sql: str, params: dict[str, object] | None = None) -> object:
    async with db.session() as session:
        return (await session.execute(text(sql), params or {})).scalar()


async def _cleanup(admin_db: DatabaseSession, control_db: DatabaseSession) -> None:
    names = await _table_names(admin_db)
    successors = f"(SELECT id FROM agents WHERE registered_by = '{_SUCCESSOR_REGISTRAR}')"
    for table, column in (
        ("actor_scope_grants", "actor_id"),
        ("agent_credential_bindings", "agent_id"),
        ("access_tokens", "actor_id"),
        ("refresh_tokens", "actor_id"),
        ("agent_credentials", "agent_id"),
    ):
        await _exec(admin_db, f"DELETE FROM {table} WHERE {column} LIKE '%p4test%'")
        await _exec(admin_db, f"DELETE FROM {table} WHERE {column} IN {successors}")
    if "service_accounts" in names:
        await _exec(admin_db, "DELETE FROM service_account_credentials")
        await _exec(admin_db, "DELETE FROM service_accounts")
        await _exec(admin_db, "DELETE FROM service_account_migration_acks")
    await _exec(admin_db, f"DELETE FROM agents WHERE registered_by = '{_SUCCESSOR_REGISTRAR}'")
    await _exec(admin_db, "DELETE FROM agents WHERE id LIKE '%p4test%'")
    await _exec(admin_db, "DELETE FROM audit_entries WHERE actor_id = 'migrate-service-accounts'")
    await _exec(admin_db, "DELETE FROM users WHERE id = :id", {"id": _OWNER})
    await _exec(
        control_db, "DELETE FROM agent_permission_rules WHERE credential_id = :c", {"c": _CRED}
    )
    await _exec(control_db, "DELETE FROM credentials WHERE id = :c", {"c": _CRED})


@pytest.fixture()
async def restore_admin_head(
    integration_config: AppConfig, admin_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Whatever a test does to the admin chain, leave it at head and clean."""
    yield
    await _cleanup(admin_db, control_db)
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


@pytest.fixture()
def runner_config(
    integration_config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point the runner's ``load_config()`` at the integration databases."""
    databases: dict[str, object] = {}
    for name in ("registry", "admin", "control"):
        db = getattr(integration_config.databases, name)
        doc = db.model_dump(mode="json", exclude_none=True)
        doc["password"] = db.password.get_secret_value()
        databases[name] = doc
    path = tmp_path / "jentic-one.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "databases": databases,
                "credentials": {
                    "encryption": {
                        "active_id": "v1",
                        "entries": [{"id": "v1", "material": _TEST_ENCRYPTION_KEY}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(path))
    yield


async def _downgrade(
    integration_config: AppConfig, admin_db: DatabaseSession, control_db: DatabaseSession
) -> None:
    """Admin below the drop the snapshot-restore way (the drop's downgrade raises)."""
    await asyncio.to_thread(restore_pre_sa_drop_admin, integration_config)
    await _cleanup(admin_db, control_db)


async def _upgrade(integration_config: AppConfig) -> None:
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def _run_full_upgrade() -> int:
    """The deployment Job's full upgrade (upgrade steps isolated out)."""
    return await asyncio.to_thread(run_mod.main, ["--skip-upgrade-steps"])


async def _admin_revision(admin_db: DatabaseSession) -> str:
    return str(await _scalar(admin_db, "SELECT version_num FROM alembic_version"))


async def _seed_owner(admin_db: DatabaseSession) -> None:
    await _exec(
        admin_db,
        "INSERT INTO users (id, email, first_name, last_name)"
        " VALUES (:id, 'p4test@test.local', 'P', 'Four')",
        {"id": _OWNER},
    )


async def _seed_sa(
    admin_db: DatabaseSession,
    *,
    sa_id: str = _SA,
    status: str = "active",
    api_key: str | None = None,
    scopes: tuple[str, ...] = (),
    credential_ids: tuple[str, ...] = (),
    stamp: str | None = None,
) -> None:
    """One raw-SQL service account (the ORM model is gone) with satellites."""
    await _exec(
        admin_db,
        "INSERT INTO service_accounts (id, name, owner_id, registered_by, status,"
        " migrated_to_actor_id, migrated_at, created_by)"
        " VALUES (:id, :name, :owner, :owner, :status, :stamp, :ts, :owner)",
        {
            "id": sa_id,
            "name": f"legacy-{sa_id}",
            "owner": _OWNER,
            "status": status,
            "stamp": stamp,
            "ts": _STAMPED_AT if stamp else None,
        },
    )
    await _exec(
        admin_db,
        "INSERT INTO service_account_credentials (id, service_account_id, api_key_hash,"
        " created_by) VALUES (:id, :sa, :digest, :owner)",
        {
            "id": f"sac_{sa_id[4:]}",
            "sa": sa_id,
            "digest": _digest(api_key) if api_key else None,
            "owner": _OWNER,
        },
    )
    for scope in scopes:
        await _exec(
            admin_db,
            "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, granted_by,"
            " created_by) VALUES (:id, :sa, 'service_account', :scope, :by, :by)",
            {"id": f"asg_{sa_id[4:]}_{scope[:6]}", "sa": sa_id, "scope": scope, "by": _OWNER},
        )
    for credential_id in credential_ids:
        await _exec(
            admin_db,
            "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, created_by)"
            " VALUES (:id, :sa, :cred, :by)",
            {"id": f"acb_{sa_id[4:]}", "sa": sa_id, "cred": credential_id, "by": _OWNER},
        )


async def _seed_rule_credential(control_db: DatabaseSession, *rule_holders: str) -> None:
    async with control_db.session() as session:
        session.add(
            Credential(
                id=_CRED,
                type=StoredCredentialType.API_KEY,
                name="p4test inline-rule credential",
                api_vendor="stripe",
                api_name="payments",
                api_version="v1",
            )
        )
        await session.commit()
    for holder in rule_holders:
        for sequence, effect in enumerate(("allow", "deny")):
            await _exec(
                control_db,
                "INSERT INTO agent_permission_rules (id, agent_id, credential_id, effect,"
                " methods, path, sequence, created_by)"
                " VALUES (:id, :agent, :cred, :effect, :methods, '.*', :seq, :by)",
                {
                    "id": f"apr_{holder[-10:]}_{sequence}",
                    "agent": holder,
                    "cred": _CRED,
                    "effect": effect,
                    "methods": json.dumps(["GET"]),
                    "seq": sequence,
                    "by": _OWNER,
                },
            )


async def _rule_holders(control_db: DatabaseSession) -> dict[str, int]:
    async with control_db.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT agent_id, count(*) AS n FROM agent_permission_rules"
                    " WHERE credential_id = :c GROUP BY agent_id"
                ),
                {"c": _CRED},
            )
        ).all()
    return {str(r.agent_id): int(r.n) for r in rows}


async def _successor_of(admin_db: DatabaseSession, name: str) -> str | None:
    value = await _scalar(
        admin_db,
        "SELECT id FROM agents WHERE registered_by = :by AND name = :name",
        {"by": _SUCCESSOR_REGISTRAR, "name": name},
    )
    return None if value is None else str(value)


# ------------------------------------------------------------------- runner


async def test_full_upgrade_migrates_verifies_and_drops_and_refuses_the_sak_key(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
    runner_config: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unmigrated SAs (an active one with grant, binding and inline rules, one
    holding a converted ``jntc_live_`` toolkit key, a pending one) → one full
    upgrade migrates, verifies, sweeps and drops. The ``sak_`` key stops
    authenticating (0.41); the ``jntc_live_`` key keeps resolving as its
    successor; stdout carries the SA → successor WARNING summary."""
    await _downgrade(integration_config, admin_db, control_db)
    await _seed_owner(admin_db)
    plaintext = "sak_p4test_live_key"
    toolkit_plaintext = "jntc_live_p4test_toolkit_key"
    toolkit_sa = "sva_p4test_toolkit"
    await _seed_sa(
        admin_db,
        api_key=plaintext,
        scopes=("capabilities:execute", "service-accounts:read"),
        credential_ids=(_CRED,),
    )
    await _seed_sa(
        admin_db, sa_id=toolkit_sa, api_key=toolkit_plaintext, scopes=("capabilities:execute",)
    )
    await _seed_sa(admin_db, sa_id="sva_p4test_pending", status="pending")
    await _seed_rule_credential(control_db, _SA)
    resolver = ApiKeyResolver(admin_db)
    assert await resolver.resolve(plaintext) is None  # no SA fallback since Phase 4

    assert await _run_full_upgrade() == 0

    names = await _table_names(admin_db)
    assert not set(_SA_TABLES) & names
    assert await _admin_revision(admin_db) != _ADMIN_PRE_DROP

    successor = await _successor_of(admin_db, f"service-account:{_SA}")
    assert successor is not None and successor.startswith("agnt_")
    # The successor carries the copied digest, yet the sak_ key is refused.
    assert await resolver.resolve(plaintext) is None
    toolkit_successor = await _successor_of(admin_db, f"service-account:{toolkit_sa}")
    assert toolkit_successor is not None
    identity = await resolver.resolve(toolkit_plaintext)
    assert identity is not None
    assert identity.sub == toolkit_successor
    assert identity.actor_type is ActorType.AGENT
    assert identity.permissions == ["capabilities:execute"]
    grants = await _scalar(
        admin_db,
        "SELECT scope FROM actor_scope_grants WHERE actor_id = :a",
        {"a": successor},
    )
    assert grants == "capabilities:execute"  # retired scope not carried

    out = capsys.readouterr().out
    assert "WARNING (service-account retirement)" in out
    assert "no longer authenticate" in out
    assert "jak_" in out
    assert f"{_SA} -> {successor}" in out
    assert f"{toolkit_sa} -> {toolkit_successor}" in out
    assert "sva_p4test_pending -> no successor agent" in out
    assert plaintext not in out and _digest(plaintext) not in out  # no secrets

    # The successor holds the binding and the inline rules; no sva_ row survives.
    assert await _rule_holders(control_db) == {successor: 2}
    bindings = await _scalar(
        admin_db,
        "SELECT count(*) FROM agent_credential_bindings WHERE agent_id = :a AND credential_id = :c",
        {"a": successor, "c": _CRED},
    )
    assert bindings == 1
    for table, column in (
        ("actor_scope_grants", "actor_id"),
        ("agent_credential_bindings", "agent_id"),
    ):
        left = await _scalar(
            admin_db, f"SELECT count(*) FROM {table} WHERE substr({column}, 1, 4) = 'sva_'"
        )
        assert left == 0, table
    # The pending SA got no successor.
    assert await _successor_of(admin_db, "service-account:sva_p4test_pending") is None


async def test_failed_migration_refuses_names_the_sa_drops_nothing_and_rerun_succeeds(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
    runner_config: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An SA whose API-key digest already sits on another agent's credential
    cannot be migrated (``uq_agent_credentials_api_key_hash``): the runner
    exits 4 naming the SA, admin stays before the drop with every SA-side
    original in place. Once the operator resolves it (here: clears the
    conflicting credential), the re-run completes the drop."""
    await _downgrade(integration_config, admin_db, control_db)
    await _seed_owner(admin_db)
    clash = "jntc_live_p4test_clash"
    await _exec(
        admin_db,
        "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by)"
        " VALUES (:id, 'p4-other-agent', :owner, :owner, 'active', :owner)",
        {"id": _AGENT, "owner": _OWNER},
    )
    await _exec(
        admin_db,
        "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by)"
        " VALUES ('agc_p4test', :id, :digest, 'system:test')",
        {"id": _AGENT, "digest": _digest(clash)},
    )
    await _seed_sa(admin_db, api_key=clash, scopes=("toolkit:read",))

    assert await _run_full_upgrade() == run_mod.EXIT_UPGRADE_STEP_FAILED

    captured = capsys.readouterr()
    err = captured.err
    assert "Refusing to retire the service accounts" in err
    assert _SA in err
    assert "migration failed" in err
    assert "Nothing was swept or dropped" in err
    assert "WARNING (service-account retirement)" not in captured.out
    assert set(_SA_TABLES) <= await _table_names(admin_db)
    assert await _admin_revision(admin_db) == _ADMIN_PRE_DROP
    status = await _scalar(
        admin_db, "SELECT status FROM service_accounts WHERE id = :id", {"id": _SA}
    )
    assert status == "active"  # not swept
    sa_grants = await _scalar(
        admin_db, "SELECT count(*) FROM actor_scope_grants WHERE actor_id = :id", {"id": _SA}
    )
    assert sa_grants == 1

    # The fix: the conflicting credential is cleared.
    await _exec(admin_db, "DELETE FROM agent_credentials WHERE agent_id = :a", {"a": _AGENT})
    assert await _run_full_upgrade() == 0
    assert not set(_SA_TABLES) & await _table_names(admin_db)


async def test_orphan_sva_rows_are_cleaned_on_both_databases(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
    runner_config: None,
) -> None:
    """``sva_`` ids with no SA row (a hand-deleted service account) leave
    grants, bindings, tokens and control-DB inline rules behind; the retirement
    deletes the control rules and the drop revision the admin rows."""
    await _downgrade(integration_config, admin_db, control_db)
    orphan = "sva_p4test_orphan"
    await _exec(
        admin_db,
        "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
        " VALUES ('asg_p4test_orphan', :id, 'service_account', 'toolkit:read')",
        {"id": orphan},
    )
    await _exec(
        admin_db,
        "INSERT INTO agent_credential_bindings (id, agent_id, credential_id)"
        " VALUES ('acb_p4test_orphan', :id, :c)",
        {"id": orphan, "c": _CRED},
    )
    await _exec(
        admin_db,
        "INSERT INTO access_tokens (id, token_hash, actor_id, actor_type, scopes,"
        " token_family_id, expires_at)"
        " VALUES ('at_p4test_orphan', 'hash-p4test-orphan', :id, 'service_account', :scopes,"
        " 'fam_p4test', :exp)",
        {
            "id": orphan,
            "scopes": json.dumps(["toolkit:read"]),
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
    )
    await _seed_rule_credential(control_db, orphan)

    assert await _run_full_upgrade() == 0

    assert not set(_SA_TABLES) & await _table_names(admin_db)
    assert await _rule_holders(control_db) == {}
    for table in ("actor_scope_grants", "agent_credential_bindings", "access_tokens"):
        left = await _scalar(
            admin_db,
            f"SELECT count(*) FROM {table} WHERE "
            + ("agent_id" if table == "agent_credential_bindings" else "actor_id")
            + " = :id",
            {"id": orphan},
        )
        assert left == 0, table


async def test_empty_install_drops_and_a_repeated_run_is_a_noop(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
    runner_config: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No service accounts → the retirement has nothing to do and the drop
    proceeds; once past the drop the runner never retires again."""
    await _downgrade(integration_config, admin_db, control_db)

    assert await _run_full_upgrade() == 0
    out = capsys.readouterr().out
    assert "==> service-account retirement: retired" in out
    assert not set(_SA_TABLES) & await _table_names(admin_db)
    head = await _admin_revision(admin_db)

    assert await asyncio.to_thread(run_mod.sa_retirement_pending) is False
    assert await _run_full_upgrade() == 0
    assert "service-account retirement" not in capsys.readouterr().out
    assert await _admin_revision(admin_db) == head


async def test_post_stamp_rows_are_healed_onto_the_earlier_successor(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
    runner_config: None,
) -> None:
    """An SA stamped by an earlier (0.40) run gains a grant and a binding
    after its stamp: the retirement copies exactly those onto the successor.
    A pre-stamp grant the operator removed from the successor is NOT
    resurrected."""
    await _downgrade(integration_config, admin_db, control_db)
    await _seed_owner(admin_db)
    await _exec(
        admin_db,
        "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by)"
        " VALUES (:id, 'p4-successor', :owner, :by, 'active', :by)",
        {"id": _AGENT, "owner": _OWNER, "by": _SUCCESSOR_REGISTRAR},
    )
    # Stamped earlier; the pre-stamp grant 'toolkit:read' was twinned then and
    # since removed from the successor by the operator (no agent row for it).
    await _seed_sa(admin_db, scopes=("toolkit:read",), stamp=_AGENT)
    await _exec(
        admin_db,
        "UPDATE actor_scope_grants SET created_at = :before WHERE actor_id = :sa",
        {"before": _STAMPED_AT - dt.timedelta(days=1), "sa": _SA},
    )
    after_stamp = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)  # clock-drift proof
    await _exec(
        admin_db,
        "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, created_at)"
        " VALUES ('asg_p4test_late', :sa, 'service_account', 'capabilities:execute', :ts)",
        {"sa": _SA, "ts": after_stamp},
    )
    await _exec(
        admin_db,
        "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, created_at)"
        " VALUES ('acb_p4test_late', :sa, :c, :ts)",
        {"sa": _SA, "c": _CRED, "ts": after_stamp},
    )

    assert await _run_full_upgrade() == 0

    assert not set(_SA_TABLES) & await _table_names(admin_db)
    async with admin_db.session() as session:
        scopes = {
            str(r.scope)
            for r in (
                await session.execute(
                    text("SELECT scope FROM actor_scope_grants WHERE actor_id = :a"), {"a": _AGENT}
                )
            ).all()
        }
    assert scopes == {"capabilities:execute"}  # healed; the removed one stays removed
    bindings = await _scalar(
        admin_db,
        "SELECT count(*) FROM agent_credential_bindings WHERE agent_id = :a AND credential_id = :c",
        {"a": _AGENT, "c": _CRED},
    )
    assert bindings == 1
    audit = await _scalar(
        admin_db,
        "SELECT count(*) FROM audit_entries WHERE actor_id = 'migrate-service-accounts'"
        " AND reason = 'theme8_sa_retirement_post_stamp_copy' AND target_id = :a",
        {"a": _AGENT},
    )
    assert audit == 1


# ------------------------------------------------------ the revision's gate


async def test_targeted_upgrade_skipping_the_retirement_is_refused_by_id(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """Plain Alembic (no runner retirement): the drop refuses unfinished rows,
    names them, points at the full runner, and drops nothing."""
    await _downgrade(integration_config, admin_db, control_db)
    await _seed_owner(admin_db)
    await _seed_sa(admin_db, api_key="sak_p4test_unmigrated")
    await _seed_sa(admin_db, sa_id="sva_p4test_unswept", status="active", stamp="skipped")

    with pytest.raises(Exception, match="Refusing to drop the service-account tables") as exc_info:
        await _upgrade(integration_config)
    message = str(exc_info.value)
    assert f"1 unmigrated service account(s): {_SA}" in message
    assert "1 migrated but unswept service account(s): sva_p4test_unswept" in message
    assert "Nothing was dropped" in message
    assert "python -m jentic_one.migrations.run" in message
    assert set(_SA_TABLES) <= await _table_names(admin_db)


async def test_drop_sweeps_retired_scope_strings(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """The retired ``service-accounts:*`` strings are purged from grant and
    token surfaces; unrelated scopes survive untouched."""
    await _downgrade(integration_config, admin_db, control_db)
    await _seed_owner(admin_db)
    await _exec(
        admin_db,
        "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by)"
        " VALUES (:id, 'p4-successor', :owner, 'system:test', 'active', 'system:test')",
        {"id": _AGENT, "owner": _OWNER},
    )
    await _exec(
        admin_db,
        "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
        " VALUES ('asg_p4test_a', :agent, 'agent', 'owner:service-accounts:read'),"
        "        ('asg_p4test_b', :agent, 'agent', 'agents:read')",
        {"agent": _AGENT},
    )
    await _exec(
        admin_db,
        "INSERT INTO access_tokens (id, token_hash, actor_id, actor_type, scopes,"
        " token_family_id, expires_at, revoked_at)"
        " VALUES ('at_p4test_2', 'hash-p4test-2', :agent, 'agent', :scopes,"
        " 'fam_p4test', :exp, NULL)",
        {
            "agent": _AGENT,
            "scopes": json.dumps(["service-accounts:read", "agents:read"]),
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
    )

    await _upgrade(integration_config)

    async with admin_db.session() as session:
        grants = {
            row.scope
            for row in (
                await session.execute(
                    text("SELECT scope FROM actor_scope_grants WHERE actor_id = :a"),
                    {"a": _AGENT},
                )
            ).all()
        }
        raw_scopes = (
            await session.execute(text("SELECT scopes FROM access_tokens WHERE id = 'at_p4test_2'"))
        ).scalar_one()
    assert grants == {"agents:read"}
    scopes = json.loads(raw_scopes) if isinstance(raw_scopes, str) else raw_scopes
    assert scopes == ["agents:read"]


async def test_downgrade_is_irreversible_and_changes_nothing(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """The drop never recreates empty tables: its downgrade raises, pointing at
    the pre-upgrade snapshot, and leaves admin at the drop revision."""
    # Walk any reversible revisions stacked on the drop back first, so the
    # downgrade below exercises the drop itself.
    await asyncio.to_thread(command.downgrade, _admin_cfg(integration_config), _ADMIN_DROP)
    assert await _admin_revision(admin_db) == _ADMIN_DROP
    with pytest.raises(RuntimeError, match="irreversible") as excinfo:
        await asyncio.to_thread(command.downgrade, _admin_cfg(integration_config), _ADMIN_PRE_DROP)
    assert "restore the admin database from the pre-upgrade snapshot" in str(excinfo.value)
    assert await _admin_revision(admin_db) == _ADMIN_DROP
    assert not set(_SA_TABLES) & await _table_names(admin_db)
