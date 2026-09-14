"""Integration tests for the theme-5 Phase 6a toolkit export/import job (P-01).

Round-trips the five doomed tables: seed → export → simulate the Phase-6b
drop (delete every row) → import → deep equality of a fresh export against
the original document. Also covers import idempotency (partial-failure
re-run) and the self-describing-format validation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, text

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.services.toolkit_export import (
    ToolkitExportError,
    ToolkitExportService,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_TABLES = (
    "toolkits",
    "toolkit_keys",
    "toolkit_credential_bindings",
    "toolkit_permission_rules",
    "agent_toolkit_bindings",
)

_BOUND_AT = dt.datetime(2026, 3, 1, 8, 30, tzinfo=dt.UTC)


@pytest.fixture()
async def clean_tables(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Wipe the five exported tables (the job exports them whole)."""

    async def _cleanup() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ToolkitPermissionRule))
            await session.execute(delete(ToolkitKey))
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Toolkit))
            await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_extest%'"))
            await session.commit()
        async with admin_db.session() as session:
            await session.execute(text("DELETE FROM agent_toolkit_bindings"))
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _seed(control_db: DatabaseSession, admin_db: DatabaseSession) -> None:
    """A row in every exported table, exercising the awkward value shapes:
    JSON lists, NULLs, booleans, and explicit timestamps."""
    async with control_db.session() as session:
        session.add(Toolkit(id="tk_extest_1", name="ex-toolkit", description=None, active=False))
        session.add(
            Credential(
                id="cred_extest_1",
                type="token_value",
                name="ex-cred",
                api_vendor="extest.local",
            )
        )
        await session.flush()
        session.add(
            ToolkitKey(
                id="ck_extest_1",
                toolkit_id="tk_extest_1",
                hashed_key="argon2-extest",
                key_preview="jntc_live_ex...",
                lookup_hash="extest-lookup",
                allowed_ips=["10.0.0.1", "10.0.0.2"],
                revoked=True,
                migrated_actor_id="sva_extest_1",
                last_used_at=_BOUND_AT,
                created_by="usr_extest",
            )
        )
        session.add(
            ToolkitCredentialBinding(
                id="tcb_extest_1",
                toolkit_id="tk_extest_1",
                credential_id="cred_extest_1",
                bound_at=_BOUND_AT,
            )
        )
        session.add(
            ToolkitPermissionRule(
                id="tpr_extest_1",
                toolkit_id="tk_extest_1",
                credential_id="cred_extest_1",
                effect="allow",
                methods=["GET", "POST"],
                path="/v1/.*",
                match_mode="regex",
                operations=None,
                sequence=0,
                comment="ex-rule",
            )
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
    async with control_db.session() as session:
        await session.execute(delete(ToolkitPermissionRule))
        await session.execute(delete(ToolkitKey))
        await session.execute(delete(ToolkitCredentialBinding))
        await session.execute(delete(Toolkit))
        await session.commit()
    async with admin_db.session() as session:
        await session.execute(text("DELETE FROM agent_toolkit_bindings"))
        await session.commit()


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
