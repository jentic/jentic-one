"""add connect-session target kinds, open-API dedupe index and outcomes table

``connect_sessions`` gains a ``target_kind`` (``vendor`` | ``api``, server
default ``vendor`` so every existing row stays a vendor target), the API
identity of an ``api`` target (``api_name``, ``api_version``) and snapshots of
its declared security scheme and canonical hosts. A partial unique index keeps
one open session per agent and API identity (``api`` targets only).

``connect_session_outcomes`` is a new FK-less, append-only table recording how
each session ended — the session row itself is cascade-deleted with its
pending credential on a terminal transition.

Existing rows are all ``vendor`` targets, so the partial index cannot collide
on upgrade. The matching dedupe index for OAuth ``vendor`` targets is not part
of this revision: open duplicate OAuth sessions are legal today, so that index
ships with the code that rotates a repeat request onto the open session.

Downgrade removes ``api`` targets before dropping the columns, since an older
release reads every row as a vendor-registry key: a live ``api`` session and
its pending credential are deleted; a connected one loses only the session row
(its credential is a normal connected credential by then).

Revision ID: bb2c3d4e5f6a
Revises: aa1b2c3d4e5f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bb2c3d4e5f6a"  # pragma: allowlist secret
down_revision: str | None = "aa1b2c3d4e5f"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN_API_TARGET_PREDICATE = (
    "target_kind = 'api' AND state IN ('created', 'awaiting_app', 'polling') "
    "AND agent_id IS NOT NULL"
)

_SNAPSHOT_COLUMNS = (
    "api_name",
    "api_version",
    "scheme_type",
    "scheme_location",
    "scheme_field_name",
    "pinned_hosts",
)


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    json_type = sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

    op.add_column(
        "connect_sessions",
        sa.Column(
            "target_kind",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'vendor'"),
        ),
    )
    op.add_column("connect_sessions", sa.Column("api_name", sa.String(255), nullable=True))
    op.add_column("connect_sessions", sa.Column("api_version", sa.String(100), nullable=True))
    op.add_column("connect_sessions", sa.Column("scheme_type", sa.String(50), nullable=True))
    op.add_column("connect_sessions", sa.Column("scheme_location", sa.String(20), nullable=True))
    op.add_column("connect_sessions", sa.Column("scheme_field_name", sa.String(255), nullable=True))
    op.add_column("connect_sessions", sa.Column("pinned_hosts", json_type, nullable=True))
    op.create_index(
        "ix_connect_sessions_open_api_target",
        "connect_sessions",
        ["agent_id", "vendor", "api_name", "api_version"],
        unique=True,
        postgresql_where=sa.text(_OPEN_API_TARGET_PREDICATE),
        sqlite_where=sa.text(_OPEN_API_TARGET_PREDICATE),
    )

    op.create_table(
        "connect_session_outcomes",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("cso") if pg else None,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("session_id", sa.String(30), nullable=False),
        sa.Column("agent_id", sa.String(30), nullable=True),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("vendor", sa.String(255), nullable=False),
        sa.Column("api_name", sa.String(255), nullable=True),
        sa.Column("api_version", sa.String(100), nullable=True),
        sa.Column("resolved_flow", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("poll_token_hash", sa.String(64), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_connect_session_outcomes_created_at", "connect_session_outcomes", ["created_at"]
    )
    op.create_index(
        "ix_connect_session_outcomes_created_by", "connect_session_outcomes", ["created_by"]
    )
    op.create_index(
        "ix_connect_session_outcomes_session_id",
        "connect_session_outcomes",
        ["session_id"],
        unique=True,
    )
    op.create_index(
        "ix_connect_session_outcomes_ended_at", "connect_session_outcomes", ["ended_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_connect_session_outcomes_ended_at", table_name="connect_session_outcomes")
    op.drop_index("ix_connect_session_outcomes_session_id", table_name="connect_session_outcomes")
    op.drop_index("ix_connect_session_outcomes_created_by", table_name="connect_session_outcomes")
    op.drop_index("ix_connect_session_outcomes_created_at", table_name="connect_session_outcomes")
    op.drop_table("connect_session_outcomes")

    # Remove ``api`` targets while the column still tells them apart. A live
    # session's pending credential goes with it; a connected session's
    # credential stays. The session rows are deleted explicitly as well, so the
    # result does not depend on the FK cascade being enforced (SQLite).
    op.execute(
        sa.text(
            "DELETE FROM credentials WHERE state = 'pending' AND id IN "
            "(SELECT credential_id FROM connect_sessions WHERE target_kind = 'api')"
        )
    )
    op.execute(sa.text("DELETE FROM connect_sessions WHERE target_kind = 'api'"))

    op.drop_index("ix_connect_sessions_open_api_target", table_name="connect_sessions")
    for column in reversed(_SNAPSHOT_COLUMNS):
        op.drop_column("connect_sessions", column)
    op.drop_column("connect_sessions", "target_kind")
