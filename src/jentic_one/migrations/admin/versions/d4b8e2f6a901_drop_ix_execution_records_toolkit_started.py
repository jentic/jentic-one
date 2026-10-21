"""drop ix_execution_records_toolkit_started (#1796)

Nothing reads ``execution_records`` by ``(toolkit_id, started_at)`` in steady
state: the ``toolkit_id`` filters on ``GET /executions`` and
``GET /monitoring/usage`` are removed (#1745), and repeated-failure detection
(``shared/events/repeated_failure.py``) keys on ``credential_id`` only, covered
by ``ix_execution_records_repeated_failure_credential_scan``. Dropping
``ix_execution_records_toolkit_started`` removes its per-insert maintenance cost
while keeping ``execution_records.toolkit_id`` for historical attribution.

Both the drop and the downgrade rebuild run ``CONCURRENTLY`` in autocommit
blocks on PostgreSQL so the migration never holds a write-blocking lock on
``execution_records``. A ``CONCURRENTLY`` build that fails on PostgreSQL leaves
an INVALID index of the same name behind, so ``downgrade()`` first drops any
INVALID leftover (a valid index is never touched) before ``CREATE INDEX
CONCURRENTLY IF NOT EXISTS``, and ``upgrade()`` drops with ``IF EXISTS``:
re-running a half-applied upgrade or downgrade succeeds and ends with a valid
state. On SQLite the concurrency flag is ignored and the plain index is dropped
and recreated.

Revision ID: d4b8e2f6a901
Revises: eb3688e956fc
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8e2f6a901"  # pragma: allowlist secret
down_revision: str | None = "eb3688e956fc"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "execution_records"
_INDEX = "ix_execution_records_toolkit_started"


def _drop_invalid_leftover(index_name: str) -> None:
    """Drop ``index_name`` if a failed concurrent build left it INVALID (PostgreSQL only)."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    invalid = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND c.relnamespace = to_regnamespace(current_schema()) "
            "AND NOT i.indisvalid"
        ),
        {"name": index_name},
    ).scalar()
    if invalid:
        op.drop_index(index_name, table_name=_TABLE, postgresql_concurrently=True, if_exists=True)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(_INDEX, table_name=_TABLE, postgresql_concurrently=True, if_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _drop_invalid_leftover(_INDEX)
        op.create_index(
            _INDEX,
            _TABLE,
            ["toolkit_id", "started_at"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
