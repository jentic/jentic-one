"""backfill revision 1 for open connect sessions

The data step paired with ``dc9d3cd8f0b7``. Every open session (``created``,
``awaiting_app`` or ``polling``) without a revision gets revision 1 built
from its ``requested_permission_rules``, ``requested_scopes`` and ``reason``,
and ``current_revision_id`` points at it, so a reviewer can confirm it by
revision like any new session. Ended sessions are left alone.

Downgrading changes no data: ``dc9d3cd8f0b7`` drops the table and column.

Revision ID: d9305dcd765e
Revises: dc9d3cd8f0b7
Create Date: 2026-10-16

"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from jentic_one.shared.db.ids import generate_ksuid

revision: str = "d9305dcd765e"  # pragma: allowlist secret
down_revision: str | None = "dc9d3cd8f0b7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json(raw: Any, empty: Any) -> Any:
    if raw is None:
        return empty
    if isinstance(raw, str):
        return json.loads(raw) if raw else empty
    return raw


def _digest(result: dict[str, Any], reason: str | None) -> str:
    # Must match ``control/services/integrations/revisions.py::revision_digest``.
    encoded = json.dumps(
        {"baseline": None, "result": result, "reason": reason},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, initiator_actor_id, requested_permission_rules, requested_scopes, reason "
            "FROM connect_sessions "
            "WHERE state IN ('created', 'awaiting_app', 'polling') "
            "AND current_revision_id IS NULL"
        )
    ).all()
    json_param = sa.JSON() if conn.dialect.name == "sqlite" else postgresql.JSONB()
    insert = sa.text(
        "INSERT INTO connect_session_revisions "
        "(id, created_at, created_by, session_id, number, proposal, result, digest) "
        "VALUES (:id, :now, :created_by, :session_id, 1, :proposal, :result, :digest)"
    ).bindparams(
        sa.bindparam("now", type_=sa.DateTime(timezone=True)),
        sa.bindparam("proposal", type_=json_param),
        sa.bindparam("result", type_=json_param),
    )
    now = datetime.now(UTC)
    for row in rows:
        rules = _json(row.requested_permission_rules, [])
        scopes = [str(s) for s in _json(row.requested_scopes, [])]
        result = {"rules": rules, "scopes": sorted(set(scopes))}
        revision_id = generate_ksuid("csr")
        conn.execute(
            insert,
            {
                "id": revision_id,
                "now": now,
                "created_by": row.initiator_actor_id,
                "session_id": row.id,
                "proposal": {"permission_rules": rules, "scopes": scopes, "reason": row.reason},
                "result": result,
                "digest": _digest(result, row.reason),
            },
        )
        conn.execute(
            sa.text("UPDATE connect_sessions SET current_revision_id = :rid WHERE id = :id"),
            {"rid": revision_id, "id": row.id},
        )


def downgrade() -> None:
    """No data changes on downgrade: ``dc9d3cd8f0b7`` drops the revisions."""
