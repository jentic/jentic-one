"""Persistent audit log for credential / toolkit / OAuth lifecycle events.

The existing `jentic.audit` Python logger remains the operational sink (stdout
and any external aggregators a deployment configures). `persist_audit()` adds
a durable write into the `audit_events` table so the UI can render an actual
audit panel without scraping log files.

Callers should use `persist_audit()` instead of `audit_log.info()` for events
the UI needs to surface; legacy callsites that only need stdout output can
continue calling `audit_log.info(...)` directly.

Schema (see alembic 0008):

    audit_events(
        id TEXT PK, ts REAL, actor_kind TEXT, actor_id TEXT,
        ip TEXT, event TEXT, target_kind TEXT, target_id TEXT,
        payload_json TEXT
    )

Indexes: (target_kind, target_id, ts DESC), (ts DESC).
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from src.db import get_db


log = logging.getLogger("jentic")
audit_log = logging.getLogger("jentic.audit")


def _audit_event_id() -> str:
    """Short opaque ID for an audit row — `audit_<8 hex>`."""
    return f"audit_{secrets.token_hex(4)}"


async def persist_audit(
    *,
    event: str,
    actor_kind: str,
    actor_id: str | None = None,
    ip: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Write an audit event to both stdout (jentic.audit) and the audit_events table.

    All writes are best-effort — a failed DB write logs a warning but does not
    raise, so call sites can stay clean. The legacy formatted log line is still
    emitted so deployments tailing logs see the same data they did before.
    """
    payload = payload or {}

    audit_log.info(
        "%s actor=%s/%s target=%s/%s ip=%s payload=%s",
        event,
        actor_kind,
        actor_id or "-",
        target_kind or "-",
        target_id or "-",
        ip or "-",
        json.dumps(payload, sort_keys=True, default=str) if payload else "{}",
    )

    try:
        async with get_db() as db:
            await db.execute(
                """INSERT INTO audit_events
                   (id, ts, actor_kind, actor_id, ip, event,
                    target_kind, target_id, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _audit_event_id(),
                    time.time(),
                    actor_kind,
                    actor_id,
                    ip,
                    event,
                    target_kind,
                    target_id,
                    json.dumps(payload, default=str) if payload else None,
                ),
            )
            await db.commit()
    except Exception as exc:
        # Audit must never block the request path. Surface as a warning so
        # ops still notice that durable audit broke, but keep going.
        log.warning("persist_audit: DB write failed for event %s: %s", event, exc)


async def query_audit(
    *,
    target_kind: str | None = None,
    target_id: str | None = None,
    event: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return recent audit rows, newest first. All filters optional.

    Used by `GET /audit` to drive the credential history panel.
    """
    where = []
    params: list[Any] = []
    if target_kind:
        where.append("target_kind = ?")
        params.append(target_kind)
    if target_id:
        where.append("target_id = ?")
        params.append(target_id)
    if event:
        where.append("event = ?")
        params.append(event)
    sql = (
        "SELECT id, ts, actor_kind, actor_id, ip, event, target_kind, target_id, payload_json "
        "FROM audit_events"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 500)), max(0, offset)])

    async with get_db() as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        payload: Any = None
        if r[8]:
            try:
                payload = json.loads(r[8])
            except Exception:
                payload = None
        out.append(
            {
                "id": r[0],
                "ts": r[1],
                "actor_kind": r[2],
                "actor_id": r[3],
                "ip": r[4],
                "event": r[5],
                "target_kind": r[6],
                "target_id": r[7],
                "payload": payload,
            }
        )
    return out
