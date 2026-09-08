"""add unique dedupe index for mcp.session_started

Backstop for the check-then-insert race in ``shared/events/mcp_session.py``:
two workers can both pass the ``exists_with_data_value`` lookup before either
commits, emitting duplicate ``mcp.session_started`` events. This partial
unique index on ``(type, (data->>'session_id'))``, scoped to that one event
type, makes the losing insert fail with ``IntegrityError`` (which the emit
path tolerates), so the table holds exactly one row per session id. On
Postgres it also serves the dedupe lookup's read path. Scoped so other event
types carrying a ``session_id`` in ``data`` stay unconstrained.

No data cleanup is needed: ``mcp.session_started`` ships in the same release
as this index, so upgraded deployments cannot hold rows of this type yet.

Revision ID: f2a3b4c5d6e7
Revises: aa6b7c8d9e0f
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "aa6b7c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    session_id_expr = (
        sa.text("(data ->> 'session_id')") if pg else sa.text("json_extract(data, '$.session_id')")
    )
    op.create_index(
        "uq_events_mcp_session_started_session",
        "events",
        ["type", session_id_expr],
        unique=True,
        postgresql_where=sa.text("type = 'mcp.session_started'"),
        sqlite_where=sa.text("type = 'mcp.session_started'"),
    )


def downgrade() -> None:
    op.drop_index("uq_events_mcp_session_started_session", table_name="events")
