"""narrow the open API-target dedupe index to agent-started sessions

``ix_connect_sessions_open_api_target`` keeps one open session per agent and
API identity. It covered every open session naming an agent, so a user's
session for their agent and the agent's own ask shared one row: the second
asker was handed the first one's session with a rotated poll token. The
index now covers only sessions the agent started for itself
(``initiator_actor_id = agent_id``), like ``ix_connect_sessions_open_vendor_target``;
a user naming the agent always gets a session of their own.

Narrowing the predicate cannot create duplicates, so the upgrade only swaps
the index. The downgrade restores the wider index; where several open
sessions then share a key, the agent-started one (else the newest) is kept
and the rest are removed with their pending credentials — the same way the
targets migration's downgrade removes live API sessions.

Revision ID: dd4e5f6a7b8c
Revises: 70ff607085b4
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "dd4e5f6a7b8c"  # pragma: allowlist secret
down_revision: str | None = "70ff607085b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_connect_sessions_open_api_target"
_COLUMNS = ["agent_id", "vendor", "api_name", "api_version"]
_LIVE = "target_kind = 'api' AND state IN ('created', 'awaiting_app', 'polling') "
_AGENT_STARTED_PREDICATE = _LIVE + "AND agent_id IS NOT NULL AND initiator_actor_id = agent_id"
_ANY_INITIATOR_PREDICATE = _LIVE + "AND agent_id IS NOT NULL"


def _create_index(predicate: str) -> None:
    op.create_index(
        _INDEX,
        "connect_sessions",
        _COLUMNS,
        unique=True,
        postgresql_where=sa.text(predicate),
        sqlite_where=sa.text(predicate),
    )


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="connect_sessions")
    _create_index(_AGENT_STARTED_PREDICATE)


def _remove_duplicates_for_the_wider_index() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, credential_id, agent_id, initiator_actor_id, vendor, api_name, "
            "api_version, created_at FROM connect_sessions WHERE " + _ANY_INITIATOR_PREDICATE
        )
    ).all()
    groups: dict[tuple[Any, ...], list[Any]] = {}
    for row in rows:
        groups.setdefault((row.agent_id, row.vendor, row.api_name, row.api_version), []).append(row)
    for members in groups.values():
        members.sort(
            key=lambda r: (r.initiator_actor_id == r.agent_id, str(r.created_at), r.id),
            reverse=True,
        )
        for row in members[1:]:
            # The session row is deleted explicitly too, so the result does not
            # depend on the FK cascade being enforced (SQLite).
            conn.execute(sa.text("DELETE FROM connect_sessions WHERE id = :id"), {"id": row.id})
            conn.execute(
                sa.text("DELETE FROM credentials WHERE id = :id AND state = 'pending'"),
                {"id": row.credential_id},
            )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="connect_sessions")
    _remove_duplicates_for_the_wider_index()
    _create_index(_ANY_INITIATOR_PREDICATE)
