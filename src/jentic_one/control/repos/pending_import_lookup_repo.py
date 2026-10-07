"""Cross-DB read: the sources of import jobs still waiting to run or running.

The create-time unmatched-scope advisory (#1020) must not warn about an API
whose import is already queued — the UI and ``ensure_imported`` enqueue the
import and create the credential back to back, and events are append-only, so
a false warning would stay on the events page for good. Control may not import
admin modules (arch boundary), so — like ``registry_api_lookup_repo`` — this
runs raw SQL over the admin DB through the shared ``DatabaseSession``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.models.jobs import JobKind, JobStatus

_PENDING_IMPORT_PAYLOADS = text(
    "SELECT payload FROM jobs WHERE kind = :kind AND status IN (:queued, :running) "
    "ORDER BY created_at DESC LIMIT :limit"
)


class PendingImportLookupRepository:
    """Read-only raw-SQL lookups against the admin ``jobs`` table."""

    @staticmethod
    async def pending_import_sources(
        session: AsyncSession, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The ``sources`` entries of queued/running import jobs (newest first).

        Bounded: the import queue is normally near-empty, and the caller only
        needs to know whether *a* pending source may cover a scope.
        """
        rows = (
            await session.execute(
                _PENDING_IMPORT_PAYLOADS,
                {
                    "kind": JobKind.IMPORT.value,
                    "queued": JobStatus.QUEUED.value,
                    "running": JobStatus.RUNNING.value,
                    "limit": limit,
                },
            )
        ).all()
        sources: list[dict[str, Any]] = []
        for row in rows:
            payload = row.payload
            # JSONB decodes to a dict on Postgres; SQLite's JSON comes back as text.
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if not isinstance(payload, dict):
                continue
            for source in payload.get("sources") or []:
                if isinstance(source, dict):
                    sources.append(source)
        return sources
