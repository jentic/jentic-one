"""Repository for the theme-5 Phase 6a toolkit export/import job (P-01).

Control-side inserts go through the ORM models (Python-side KSUID defaults
are irrelevant here — exported rows carry their original ids), so one code
path serves both dialects. The admin-side ``agent_toolkit_bindings`` table
is raw SQL for the same module-boundary reason as
``FlatteningAdminRepository``.

Import is idempotent by primary key: rows whose id already exists are
skipped, so a re-run after a partial failure (control committed, admin
crashed) completes the missing side without duplicating the other.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.repos.toolkit_flattening_repo import _bind_ts

_CONTROL_MODELS: dict[str, type[Any]] = {
    "toolkits": Toolkit,
    "toolkit_keys": ToolkitKey,
    "toolkit_credential_bindings": ToolkitCredentialBinding,
    "toolkit_permission_rules": ToolkitPermissionRule,
}


class ExportControlRepository:
    """Control-DB export/import statements — flush-only, never commits."""

    @staticmethod
    async def existing_ids(session: AsyncSession, table: str) -> set[str]:
        model = _CONTROL_MODELS[table]
        result = await session.execute(select(model.id))
        return {str(i) for i in result.scalars().all()}

    @staticmethod
    async def insert_rows(session: AsyncSession, table: str, rows: list[dict[str, Any]]) -> None:
        """Insert exported rows verbatim (ids, timestamps, and all)."""
        model = _CONTROL_MODELS[table]
        for row in rows:
            session.add(model(**row))
        await session.flush()


_LIST_ATB_FULL = text(
    "SELECT id, agent_id, toolkit_id, bound_at, created_at, updated_at, created_by"
    " FROM agent_toolkit_bindings ORDER BY id"
)

_LIST_ATB_IDS = text("SELECT id FROM agent_toolkit_bindings")

_INSERT_ATB_FULL = text(
    "INSERT INTO agent_toolkit_bindings"
    " (id, agent_id, toolkit_id, bound_at, created_at, updated_at, created_by)"
    " VALUES (:id, :agent_id, :toolkit_id, :bound_at, :created_at, :updated_at, :created_by)"
)


class ExportAdminRepository:
    """Admin-DB export/import statements for ``agent_toolkit_bindings``."""

    @staticmethod
    async def list_rows(session: AsyncSession) -> list[Any]:
        """Every binding row, raw (the service owns serialization)."""
        return list((await session.execute(_LIST_ATB_FULL)).all())

    @staticmethod
    async def existing_ids(session: AsyncSession) -> set[str]:
        result = await session.execute(_LIST_ATB_IDS)
        return {str(i) for i in result.scalars().all()}

    @staticmethod
    async def insert_row(
        session: AsyncSession,
        *,
        id: str,
        agent_id: str,
        toolkit_id: str,
        bound_at: dt.datetime,
        created_at: dt.datetime,
        updated_at: dt.datetime,
        created_by: str | None,
    ) -> None:
        await session.execute(
            _INSERT_ATB_FULL,
            {
                "id": id,
                "agent_id": agent_id,
                "toolkit_id": toolkit_id,
                "bound_at": _bind_ts(session, bound_at),
                "created_at": _bind_ts(session, created_at),
                "updated_at": _bind_ts(session, updated_at),
                "created_by": created_by,
            },
        )
