"""Integration tests for the theme-5 Phase 6a toolkit export/import job (P-01).

Round-trips the five doomed tables: seed → export → simulate the Phase-6b
drop (delete every row) → import → deep equality of a fresh export against
the original document. Also covers import idempotency (partial-failure
re-run) and the self-describing-format validation.

The legacy tables were dropped at migration head (theme-5 Phase 6b), so the
module downgrades the two drop migrations first — the post-rollback state the
import tool actually targets. All legacy-table access is raw SQL: the ORM
models are gone, which is the point of the migration-independent repository.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.toolkit_export import (
    ToolkitExportError,
    ToolkitExportService,
)
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

#: Revisions just below the theme-5 Phase 6b drop migrations.
_CONTROL_PRE_DROP = "u2c3d4e5f6a7"  # pragma: allowlist secret
_ADMIN_PRE_DROP = "c0e1f2a3b4c5"  # pragma: allowlist secret

_TABLES = (
    "toolkits",
    "toolkit_keys",
    "toolkit_credential_bindings",
    "toolkit_permission_rules",
    "agent_toolkit_bindings",
)

_BOUND_AT = dt.datetime(2026, 3, 1, 8, 30, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def legacy_tables(integration_config: AppConfig) -> Iterator[None]:
    """Downgrade the 6b drop migrations so the legacy tables exist, then re-drop.

    Teardown re-upgrades to head; the drop gates pass because the suite leaves
    the tables empty (the fresh-install path of the guard).
    """
    control_cfg = _alembic_config_for("control", integration_config.databases.control)
    admin_cfg = _alembic_config_for("admin", integration_config.databases.admin)
    command.downgrade(control_cfg, _CONTROL_PRE_DROP)
    command.downgrade(admin_cfg, _ADMIN_PRE_DROP)
    yield
    command.upgrade(control_cfg, "head")
    command.upgrade(admin_cfg, "head")


async def _wipe_legacy_rows(control_db: DatabaseSession, admin_db: DatabaseSession) -> None:
    async with control_db.session() as session:
        await session.execute(text("DELETE FROM toolkit_permission_rules"))
        await session.execute(text("DELETE FROM toolkit_keys"))
        await session.execute(text("DELETE FROM toolkit_credential_bindings"))
        await session.execute(text("DELETE FROM toolkits"))
        await session.commit()
    async with admin_db.session() as session:
        await session.execute(text("DELETE FROM agent_toolkit_bindings"))
        await session.commit()


@pytest.fixture()
async def clean_tables(
    control_db: DatabaseSession, admin_db: DatabaseSession, legacy_tables: None
) -> AsyncGenerator[None, None]:
    """Wipe the five exported tables (the job exports them whole)."""

    async def _cleanup() -> None:
        await _wipe_legacy_rows(control_db, admin_db)
        async with control_db.session() as session:
            await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_extest%'"))
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _seed(control_db: DatabaseSession, admin_db: DatabaseSession) -> None:
    """A row in every exported table, exercising the awkward value shapes:
    JSON lists, NULLs, booleans, and explicit timestamps."""
    sqlite = control_db.backend.dialect_name == "sqlite"
    # JSON columns are JSONB on Postgres — a bare string bind must be cast.
    json_bind = "(:{name})" if sqlite else "CAST(:{name} AS JSONB)"

    def _ts(value: dt.datetime) -> object:
        return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f") if sqlite else value

    async with control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, description, active)"
                " VALUES ('tk_extest_1', 'ex-toolkit', NULL, :active)"
            ),
            {"active": False},
        )
        session.add(
            Credential(
                id="cred_extest_1",
                type="token_value",
                name="ex-cred",
                api_vendor="extest.local",
            )
        )
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO toolkit_keys"
                " (id, toolkit_id, hashed_key, key_preview, lookup_hash, allowed_ips,"
                "  revoked, migrated_actor_id, last_used_at, created_by)"
                " VALUES ('ck_extest_1', 'tk_extest_1', 'argon2-extest', 'jntc_live_ex...',"
                f"  'extest-lookup', {json_bind.format(name='allowed_ips')}, :revoked,"
                "  'sva_extest_1', :last_used_at, 'usr_extest')"
            ),
            {
                "allowed_ips": '["10.0.0.1", "10.0.0.2"]',
                "revoked": True,
                "last_used_at": _ts(_BOUND_AT),
            },
        )
        await session.execute(
            text(
                "INSERT INTO toolkit_credential_bindings"
                " (id, toolkit_id, credential_id, bound_at)"
                " VALUES ('tcb_extest_1', 'tk_extest_1', 'cred_extest_1', :bound_at)"
            ),
            {"bound_at": _ts(_BOUND_AT)},
        )
        await session.execute(
            text(
                "INSERT INTO toolkit_permission_rules"
                " (id, toolkit_id, credential_id, effect, methods, path, match_mode,"
                "  operations, is_system, sequence, comment)"
                " VALUES ('tpr_extest_1', 'tk_extest_1', 'cred_extest_1', 'allow',"
                f"  {json_bind.format(name='methods')}, '/v1/.*', 'regex', NULL,"
                "  :is_system, 0, 'ex-rule')"
            ),
            {"methods": '["GET", "POST"]', "is_system": False},
        )
        await session.commit()
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id, bound_at,"
                " created_by) VALUES (:id, 'agnt_extest_1', 'tk_extest_1', :bound_at,"
                " 'usr_extest')"
            ),
            {
                "id": "atb_extest_1",
                "bound_at": _BOUND_AT.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
                if admin_db.backend.dialect_name == "sqlite"
                else _BOUND_AT,
            },
        )
        await session.commit()


async def _simulate_drop(control_db: DatabaseSession, admin_db: DatabaseSession) -> None:
    """Delete every row from the five tables — the closest a test can get to
    the Phase-6b drop without running migrations mid-session."""
    await _wipe_legacy_rows(control_db, admin_db)


def _comparable(document: dict[str, Any]) -> dict[str, Any]:
    """The invariant part of an export document (drops the run timestamp)."""
    return {key: value for key, value in document.items() if key != "exported_at"}


async def test_export_import_round_trip_deep_equality(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """seed → export → drop → import → a fresh export equals the original."""
    await _seed(control_db, admin_db)
    service = ToolkitExportService(integration_context)

    document = await service.export()
    assert document["format"] == "jentic-one-toolkit-export"
    assert document["schema_version"] == 1
    assert {t: document["tables"][t]["row_count"] for t in _TABLES} == {
        "toolkits": 1,
        "toolkit_keys": 1,
        "toolkit_credential_bindings": 1,
        "toolkit_permission_rules": 1,
        "agent_toolkit_bindings": 1,
    }
    # Dialect-neutral values: ISO timestamps, JSON lists, real booleans.
    key_row = document["tables"]["toolkit_keys"]["rows"][0]
    assert key_row["allowed_ips"] == ["10.0.0.1", "10.0.0.2"]
    assert key_row["revoked"] is True
    assert dt.datetime.fromisoformat(key_row["last_used_at"]) == _BOUND_AT
    atb_row = document["tables"]["agent_toolkit_bindings"]["rows"][0]
    assert dt.datetime.fromisoformat(atb_row["bound_at"]) == _BOUND_AT

    await _simulate_drop(control_db, admin_db)
    assert _comparable(await service.export())["tables"] != document["tables"]

    outcome = await service.import_document(document)
    assert outcome.inserted == dict.fromkeys(_TABLES, 1)
    assert outcome.skipped_existing == dict.fromkeys(_TABLES, 0)

    assert _comparable(await service.export()) == _comparable(document)


async def test_import_is_idempotent_by_row_id(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """Re-importing over existing rows skips them all (partial-failure re-run)."""
    await _seed(control_db, admin_db)
    service = ToolkitExportService(integration_context)
    document = await service.export()

    outcome = await service.import_document(document)

    assert outcome.inserted == dict.fromkeys(_TABLES, 0)
    assert outcome.skipped_existing == dict.fromkeys(_TABLES, 1)
    assert _comparable(await service.export()) == _comparable(document)


async def test_import_rejects_malformed_documents(
    integration_context: Context,
    clean_tables: None,
) -> None:
    """The self-describing header is validated before any write."""
    service = ToolkitExportService(integration_context)
    document = await service.export()

    with pytest.raises(ToolkitExportError, match="not a jentic-one-toolkit-export"):
        await service.import_document({**document, "format": "something-else"})
    with pytest.raises(ToolkitExportError, match="unsupported schema_version"):
        await service.import_document({**document, "schema_version": 99})

    truncated = {
        **document,
        "tables": {
            **document["tables"],
            "toolkits": {"row_count": 5, "rows": document["tables"]["toolkits"]["rows"]},
        },
    }
    with pytest.raises(ToolkitExportError, match="row_count"):
        await service.import_document(truncated)
