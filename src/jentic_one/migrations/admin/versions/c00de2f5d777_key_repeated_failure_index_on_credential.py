"""key the repeated-failure scan index on credential_id

The repeated-failure count (``shared/events/repeated_failure.py``) filters
failed ``execution_records`` on ``actor_id``, the consumer axis, and
``operation_id`` with a range on ``started_at``. Nothing writes
``toolkit_id`` any more, so the live consumer axis is ``credential_id`` and
the toolkit-keyed partial index never matches the query. This adds
``ix_execution_records_repeated_failure_credential_scan`` on
``(actor_id, credential_id, operation_id, started_at) WHERE status = 'failed'``
and drops the toolkit-keyed ``ix_execution_records_repeated_failure_scan``. A
legacy toolkit-keyed count still has ``ix_execution_records_toolkit_started``.

Both indexes are built and dropped ``CONCURRENTLY`` in autocommit blocks so
the migration never holds a write-blocking lock on ``execution_records``. The
new index is built before the old one is dropped, so the query is always
covered. Downgrade restores the toolkit-keyed index exactly as revision
``aceeaac9b3f5`` created it (partial on PostgreSQL only).

Revision ID: c00de2f5d777
Revises: 3306fb9172f1
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c00de2f5d777"  # pragma: allowlist secret
down_revision: str | None = "3306fb9172f1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "execution_records"
_NEW_INDEX = "ix_execution_records_repeated_failure_credential_scan"
_OLD_INDEX = "ix_execution_records_repeated_failure_scan"
_PREDICATE = "status = 'failed'"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _NEW_INDEX,
            _TABLE,
            ["actor_id", "credential_id", "operation_id", "started_at"],
            unique=False,
            postgresql_where=sa.text(_PREDICATE),
            sqlite_where=sa.text(_PREDICATE),
            postgresql_concurrently=True,
        )
    with op.get_context().autocommit_block():
        op.drop_index(_OLD_INDEX, table_name=_TABLE, postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _OLD_INDEX,
            _TABLE,
            ["actor_id", "toolkit_id", "operation_id", "started_at"],
            unique=False,
            postgresql_where=sa.text(_PREDICATE),
            postgresql_concurrently=True,
        )
    with op.get_context().autocommit_block():
        op.drop_index(_NEW_INDEX, table_name=_TABLE, postgresql_concurrently=True)
