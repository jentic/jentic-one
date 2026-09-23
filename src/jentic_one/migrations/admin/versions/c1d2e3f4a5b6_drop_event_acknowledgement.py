"""drop event acknowledgement columns

Revision ID: c1d2e3f4a5b6
Revises: b9d0e1f2a3b4
Create Date: 2026-09-22

Removes the event acknowledgement feature: the ``acknowledged`` /
``acknowledged_at`` / ``acknowledged_by`` / ``acknowledgement_note`` columns and
the partial index that filtered on them. The partial index is recreated to key
on ``requires_action`` alone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"  # pragma: allowlist secret
down_revision: str | None = "b9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACK_COLUMNS = ("acknowledgement_note", "acknowledged_by", "acknowledged_at", "acknowledged")


_MCP_SESSION_INDEX = "uq_events_mcp_session_started_session"


def _drop_mcp_session_index() -> None:
    """Drop the mcp.session_started dedupe index ahead of a SQLite batch op.

    SQLite's ``batch_alter_table`` cannot reflect the expression-based partial
    unique index from ``f2a3b4c5d6e7`` (on ``(type, data->>'session_id')``), so a
    table-copy silently loses it, while an in-place ``ADD COLUMN`` keeps it.
    Dropping it explicitly first makes the outcome identical either way, and
    :func:`_create_mcp_session_index` restores it afterwards.
    """
    op.drop_index(_MCP_SESSION_INDEX, table_name="events")


def _create_mcp_session_index() -> None:
    """Recreate the mcp.session_started dedupe index verbatim (SQLite form)."""
    op.create_index(
        _MCP_SESSION_INDEX,
        "events",
        ["type", sa.text("json_extract(data, '$.session_id')")],
        unique=True,
        sqlite_where=sa.text("type = 'mcp.session_started'"),
    )


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    op.drop_index("ix_events_requires_action_unack", table_name="events")
    if is_sqlite:
        # Drop the columns first; the table-copy would otherwise clobber a
        # freshly created index. Recreate the indexes afterwards.
        _drop_mcp_session_index()
        with op.batch_alter_table("events") as batch_op:
            for column in _ACK_COLUMNS:
                batch_op.drop_column(column)
        op.create_index(
            "ix_events_requires_action",
            "events",
            ["requires_action"],
            sqlite_where=sa.text("requires_action = 1"),
        )
        _create_mcp_session_index()
    else:
        op.create_index(
            "ix_events_requires_action",
            "events",
            ["requires_action"],
            postgresql_where=sa.text("requires_action"),
        )
        for column in _ACK_COLUMNS:
            op.drop_column("events", column)


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    ack_columns = (
        sa.Column("acknowledged", sa.Boolean, server_default="false", nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(30), nullable=True),
        sa.Column("acknowledgement_note", sa.Text, nullable=True),
    )
    op.drop_index("ix_events_requires_action", table_name="events")
    if is_sqlite:
        _drop_mcp_session_index()
        with op.batch_alter_table("events") as batch_op:
            for column in ack_columns:
                batch_op.add_column(column)
        op.create_index(
            "ix_events_requires_action_unack",
            "events",
            ["requires_action"],
            sqlite_where=sa.text("requires_action = 1 AND acknowledged = 0"),
        )
        _create_mcp_session_index()
    else:
        for column in ack_columns:
            op.add_column("events", column)
        op.create_index(
            "ix_events_requires_action_unack",
            "events",
            ["requires_action"],
            postgresql_where=sa.text("requires_action AND NOT acknowledged"),
        )
