"""Pin ``6ae76df5ece5``: the retired access-request scope maps onto ``credentials:connect``.

Rows written under the theme-7-era agent baseline carry
``owner:access-requests:read`` and lack ``credentials:connect``. Without the
mapping, a DCR client registered then gets ``invalid_scope`` from ``/authorize``
as soon as it asks for the advertised ``credentials:connect``, and an agent
created then cannot start a connect session.

Each test migrates a throwaway admin database (a fresh Postgres schema, or a
fresh SQLite file) to the revision just before the mapping, seeds rows the way
the earlier code wrote them, and upgrades across it.

Sync on purpose: the migration env calls ``asyncio.run`` itself, so
``command.upgrade`` cannot run inside an async test's event loop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.db.session import get_database_url

from ..conftest import _alembic_config_for, _test_backend

pytestmark = pytest.mark.integration

_BEFORE = "d2e3f4a5b6c7"  # pragma: allowlist secret
_MAP = "6ae76df5ece5"  # pragma: allowlist secret

_RETIRED = "owner:access-requests:read"
_SUCCESSOR = "credentials:connect"
_WARNING = "==> WARNING (access-request scope retirement):"

_USER = "usr_arscope_owner"
_OLD_AGENT = "agnt_arscope_old"
_HOLDING_AGENT = "agnt_arscope_holding"
_NEW_AGENT = "agnt_arscope_new"

#: The v0.39 agent baseline a DCR client registered then stores as its ceiling
#: (``owner:toolkits:read`` already swept by the theme-5 migration).
_OLD_CEILING = [
    "capabilities:execute",
    "capabilities:read",
    "apis:read",
    "catalog:import",
    "executions:read",
    "jobs:read",
    "events:read",
    "owner:resources:read",
    "owner:agents:read",
    "owner:credentials:read",
    _RETIRED,
]


class _Target:
    """A throwaway admin database plus the handles needed to migrate and inspect it."""

    def __init__(self, db_config: DatabaseConfig, *, postgres: bool) -> None:
        self.postgres = postgres
        self.db_config = db_config
        self.schema: str | None = db_config.schema_name if postgres else None
        self.alembic: AlembicConfig = _alembic_config_for("admin", db_config)

    def engine(self) -> AsyncEngine:
        return create_async_engine(get_database_url(self.db_config))

    def array(self, values: list[str], *, native: bool = False) -> object:
        """Bind value for a scope array: ``VARCHAR[]`` columns take a list on Postgres."""
        return values if native and self.postgres else json.dumps(values)


@pytest.fixture()
def admin_target(integration_config: AppConfig, tmp_path: Path) -> Iterator[_Target]:
    base = integration_config.databases.admin
    if _test_backend() == "postgres":
        schema = f"arscope_{uuid.uuid4().hex[:8]}"
        # Superuser: the migration env creates the schema, which needs CREATE on
        # the database (the harness's per-surface users deliberately lack it).
        cfg = base.model_copy(
            update={"schema_name": schema, "user": "postgres", "password": SecretStr("postgres")}
        )
        target = _Target(cfg, postgres=True)
        try:
            yield target
        finally:

            async def _drop() -> None:
                engine = target.engine()
                try:
                    async with engine.begin() as conn:
                        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                finally:
                    await engine.dispose()

            asyncio.run(_drop())
    else:
        cfg = base.model_copy(update={"path": str(tmp_path / "admin.db")})
        yield _Target(cfg, postgres=False)


def _run(target: _Target, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    async def _go() -> list[Any]:
        engine = target.engine()
        try:
            async with engine.begin() as conn:
                if target.schema:
                    await conn.execute(text(f'SET LOCAL search_path TO "{target.schema}"'))
                result = await conn.execute(text(sql), params or {})
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _scopes(raw: object) -> list[str]:
    return list(json.loads(raw) if isinstance(raw, str) else raw)  # type: ignore[arg-type]


def _grant(target: _Target, row_id: str, actor_id: str, permission: str) -> None:
    _run(
        target,
        "INSERT INTO actor_permission_grants (id, actor_id, actor_type, permission)"
        " VALUES (:id, :actor, 'agent', :permission)",
        {"id": row_id, "actor": actor_id, "permission": permission},
    )


def _client(target: _Target, row_id: str, client_id: str, scopes: list[str] | None) -> None:
    _run(
        target,
        "INSERT INTO oauth_clients (id, client_id, name, redirect_uris, allowed_scopes)"
        " VALUES (:id, :client_id, :client_id, :uris, :scopes)",
        {
            "id": row_id,
            "client_id": client_id,
            "uris": target.array(["https://claude.ai/api/mcp/auth_callback"], native=True),
            "scopes": None if scopes is None else target.array(scopes, native=True),
        },
    )


def _seed(target: _Target) -> None:
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    _run(
        target,
        "INSERT INTO users (id, email, first_name, last_name)"
        " VALUES (:id, 'arscope@test.local', 'A', 'R')",
        {"id": _USER},
    )
    # An agent from the earlier baseline, one that already holds the successor,
    # and one created after the swap.
    _grant(target, "asg_arscope_old_retired", _OLD_AGENT, _RETIRED)
    _grant(target, "asg_arscope_old_read", _OLD_AGENT, "apis:read")
    _grant(target, "asg_arscope_hold_retired", _HOLDING_AGENT, _RETIRED)
    _grant(target, "asg_arscope_hold_connect", _HOLDING_AGENT, _SUCCESSOR)
    _grant(target, "asg_arscope_new_connect", _NEW_AGENT, _SUCCESSOR)
    _run(
        target,
        "INSERT INTO user_permission_grants (id, user_id, permission)"
        " VALUES ('upg_arscope_retired', :user, :retired),"
        " ('upg_arscope_read', :user, 'apis:read')",
        {"user": _USER, "retired": _RETIRED},
    )
    # The stale DCR client, a narrowed admin client, and an unrestricted one.
    _client(target, "oac_arscope_dcr", "oc_arscope_claude", _OLD_CEILING)
    _client(target, "oac_arscope_narrow", "oc_arscope_narrow", ["apis:read"])
    _client(target, "oac_arscope_open", "oc_arscope_open", None)
    _run(
        target,
        "INSERT INTO oauth_client_grants (id, oauth_client_id, user_id, agent_id, scopes, status)"
        " VALUES ('ocg_arscope_1', 'oc_arscope_claude', :user, :agent, :scopes, 'active')",
        {"user": _USER, "agent": _OLD_AGENT, "scopes": json.dumps(["apis:read", _RETIRED])},
    )
    for table, prefix in (("access_tokens", "at"), ("refresh_tokens", "rt")):
        _run(
            target,
            f"INSERT INTO {table} (id, token_hash, actor_id, actor_type, scopes,"
            " token_family_id, expires_at)"
            " VALUES (:id, :hash, :agent, 'agent', :scopes, 'fam_arscope', :exp)",
            {
                "id": f"{prefix}_arscope_1",
                "hash": f"hash-arscope-{prefix}",
                "agent": _OLD_AGENT,
                "scopes": json.dumps(["apis:read", _RETIRED]),
                "exp": expires,
            },
        )
    _run(
        target,
        "INSERT INTO authorization_codes"
        " (id, code_hash, user_id, client_id, redirect_uri, code_challenge, scopes, expires_at)"
        " VALUES ('ac_arscope_1', 'code-arscope', :user, 'oc_arscope_claude',"
        " 'https://claude.ai/api/mcp/auth_callback', 'chal', :scopes, :exp)",
        {"user": _USER, "scopes": f"openid apis:read {_RETIRED}", "exp": expires},
    )


def _agent_permissions(target: _Target) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in _run(target, "SELECT actor_id, permission FROM actor_permission_grants"):
        out.setdefault(row.actor_id, set()).add(row.permission)
    return out


def _array(target: _Target, table: str, column: str, row_id: str) -> list[str] | None:
    raw = _run(target, f"SELECT {column} FROM {table} WHERE id = :id", {"id": row_id})[0][0]
    return None if raw is None else _scopes(raw)


def test_maps_ceilings_and_agents_and_sweeps_the_rest(
    admin_target: _Target, capsys: pytest.CaptureFixture[str]
) -> None:
    command.upgrade(admin_target.alembic, _BEFORE)
    _seed(admin_target)
    capsys.readouterr()

    command.upgrade(admin_target.alembic, _MAP)

    # Agents: the old one gains the successor on its own row id; the one already
    # holding it loses only the retired row; the new one is untouched.
    assert _agent_permissions(admin_target) == {
        _OLD_AGENT: {_SUCCESSOR, "apis:read"},
        _HOLDING_AGENT: {_SUCCESSOR},
        _NEW_AGENT: {_SUCCESSOR},
    }
    assert _run(
        admin_target,
        "SELECT permission FROM actor_permission_grants WHERE id = 'asg_arscope_old_retired'",
    ) == [(_SUCCESSOR,)]
    # Users are swept, never widened.
    assert {
        row.permission
        for row in _run(admin_target, "SELECT permission FROM user_permission_grants")
    } == {"apis:read"}

    # Ceilings: the retired entry becomes the successor in place; a narrowed or
    # unrestricted client is left alone.
    expected = [_SUCCESSOR if s == _RETIRED else s for s in _OLD_CEILING]
    assert _array(admin_target, "oauth_clients", "allowed_scopes", "oac_arscope_dcr") == expected
    assert _array(admin_target, "oauth_clients", "allowed_scopes", "oac_arscope_narrow") == [
        "apis:read"
    ]
    assert _array(admin_target, "oauth_clients", "allowed_scopes", "oac_arscope_open") is None
    assert _array(admin_target, "oauth_client_grants", "scopes", "ocg_arscope_1") == [
        "apis:read",
        _SUCCESSOR,
    ]

    # Snapshots: swept, not mapped.
    assert _array(admin_target, "access_tokens", "scopes", "at_arscope_1") == ["apis:read"]
    assert _array(admin_target, "refresh_tokens", "scopes", "rt_arscope_1") == ["apis:read"]
    assert _run(
        admin_target, "SELECT scopes FROM authorization_codes WHERE id = 'ac_arscope_1'"
    ) == [("openid apis:read",)]

    out = capsys.readouterr().out
    warning = next(line for line in out.splitlines() if line.startswith(_WARNING))
    assert "to 1 agent(s)" in warning
    assert "1 OAuth client(s) and 1 consent grant(s)" in warning
    assert _OLD_AGENT in warning
    assert _HOLDING_AGENT not in warning
    assert "oc_arscope_claude" in warning


def test_nothing_to_map_stays_quiet_and_rerun_is_a_noop(
    admin_target: _Target, capsys: pytest.CaptureFixture[str]
) -> None:
    command.upgrade(admin_target.alembic, _BEFORE)
    _client(admin_target, "oac_arscope_fresh", "oc_arscope_fresh", ["apis:read", _SUCCESSOR])
    capsys.readouterr()

    command.upgrade(admin_target.alembic, _MAP)
    assert _WARNING not in capsys.readouterr().out
    assert _array(admin_target, "oauth_clients", "allowed_scopes", "oac_arscope_fresh") == [
        "apis:read",
        _SUCCESSOR,
    ]

    command.downgrade(admin_target.alembic, _BEFORE)
    command.upgrade(admin_target.alembic, _MAP)
    assert _WARNING not in capsys.readouterr().out
