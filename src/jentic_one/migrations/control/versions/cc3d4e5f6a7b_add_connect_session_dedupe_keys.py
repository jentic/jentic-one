"""add connect-session vendor key, OAuth dedupe index and outcome credential

``connect_sessions`` gains ``vendor_key`` (the vendor-registry key an API
target's OAuth app resolved to) and ``dedupe_key`` (a digest of an
agent-started vendor session's flow, OAuth app registration and requested
scopes). A partial unique index on ``(agent_id, vendor, dedupe_key)`` keeps
one open agent-started session per OAuth request.

Open agent-started vendor sessions are backfilled with their key before the
index is created. Where several open sessions share a key, the newest keeps
it. An older duplicate still in ``created`` (never confirmed: no binding and
no vendor conversation) is expired — an ``expired`` outcome row is written and
its pending credential and session row are deleted. An older duplicate already
in ``polling`` (a human may be mid-consent) keeps a NULL key, stays outside the
index and ends on its normal 30-minute TTL.

``connect_session_outcomes`` gains ``credential_id`` (the credential a
``connected`` session left bound) and an ``(agent_id, ended_at)`` index for
the rejection cooldown.

Revision ID: cc3d4e5f6a7b
Revises: bb2c3d4e5f6a
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from jentic_one.shared.db.ids import generate_ksuid

revision: str = "cc3d4e5f6a7b"  # pragma: allowlist secret
down_revision: str | None = "bb2c3d4e5f6a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN_VENDOR_TARGET_PREDICATE = (
    "target_kind = 'vendor' AND state IN ('created', 'awaiting_app', 'polling') "
    "AND agent_id IS NOT NULL AND dedupe_key IS NOT NULL"
)


def _dedupe_key(resolved_flow: str, registration_id: str | None, scopes: Iterable[str]) -> str:
    # Must match ``control/services/integrations/dedupe.py::oauth_dedupe_key``.
    payload = json.dumps(
        [resolved_flow, registration_id or "", sorted(set(scopes))], separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _scopes(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw) if raw else []
    return [str(s) for s in raw] if isinstance(raw, list) else []


def _backfill_and_expire_duplicates() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT cs.id, cs.agent_id, cs.vendor, cs.resolved_flow, cs.requested_scopes, "
            "cs.state, cs.created_at, cs.credential_id, cs.initiator_actor_id, cs.poll_token, "
            "c.oauth_app_registration_id "
            "FROM connect_sessions cs JOIN credentials c ON c.id = cs.credential_id "
            "WHERE cs.target_kind = 'vendor' AND cs.agent_id IS NOT NULL "
            "AND cs.initiator_actor_id = cs.agent_id "
            "AND cs.state IN ('created', 'awaiting_app', 'polling')"
        )
    ).all()
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        key = _dedupe_key(
            row.resolved_flow, row.oauth_app_registration_id, _scopes(row.requested_scopes)
        )
        groups.setdefault((row.agent_id, row.vendor, key), []).append(row)

    now = datetime.now(UTC)
    for (_agent, _vendor, key), members in groups.items():
        members.sort(key=lambda r: (str(r.created_at), r.id), reverse=True)
        newest, older = members[0], members[1:]
        conn.execute(
            sa.text("UPDATE connect_sessions SET dedupe_key = :key WHERE id = :id"),
            {"key": key, "id": newest.id},
        )
        for row in older:
            if row.state != "created":
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO connect_session_outcomes (id, created_at, created_by, "
                    "session_id, agent_id, target_kind, vendor, resolved_flow, outcome, "
                    "poll_token_hash, ended_at) VALUES (:id, :now, :created_by, :session_id, "
                    ":agent_id, 'vendor', :vendor, :resolved_flow, 'expired', :poll_token, :now)"
                ).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True))),
                {
                    "id": generate_ksuid("cso"),
                    "now": now,
                    "created_by": row.initiator_actor_id,
                    "session_id": row.id,
                    "agent_id": row.agent_id,
                    "vendor": row.vendor,
                    "resolved_flow": row.resolved_flow,
                    "poll_token": row.poll_token,
                },
            )
            # The session row is deleted explicitly too, so the result does not
            # depend on the FK cascade being enforced (SQLite).
            conn.execute(sa.text("DELETE FROM connect_sessions WHERE id = :id"), {"id": row.id})
            conn.execute(
                sa.text("DELETE FROM credentials WHERE id = :id AND state = 'pending'"),
                {"id": row.credential_id},
            )


def upgrade() -> None:
    op.add_column("connect_sessions", sa.Column("vendor_key", sa.String(255), nullable=True))
    op.add_column("connect_sessions", sa.Column("dedupe_key", sa.String(64), nullable=True))
    op.add_column(
        "connect_session_outcomes", sa.Column("credential_id", sa.String(30), nullable=True)
    )
    op.create_index(
        "ix_connect_session_outcomes_agent_ended_at",
        "connect_session_outcomes",
        ["agent_id", "ended_at"],
    )

    _backfill_and_expire_duplicates()

    op.create_index(
        "ix_connect_sessions_open_vendor_target",
        "connect_sessions",
        ["agent_id", "vendor", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(_OPEN_VENDOR_TARGET_PREDICATE),
        sqlite_where=sa.text(_OPEN_VENDOR_TARGET_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("ix_connect_sessions_open_vendor_target", table_name="connect_sessions")
    op.drop_index(
        "ix_connect_session_outcomes_agent_ended_at", table_name="connect_session_outcomes"
    )
    op.drop_column("connect_session_outcomes", "credential_id")
    op.drop_column("connect_sessions", "dedupe_key")
    op.drop_column("connect_sessions", "vendor_key")
