"""index events on (created_by, actor_id)

The event subject scoping matches ``created_by`` against the caller or one of
the caller's agents, and the actor lookup's event arm pairs that with
``actor_id`` (``admin/scoping/filters.py``). This adds
``ix_events_created_by_actor`` on ``(created_by, actor_id)`` and drops the
single-column ``ix_events_created_by``, whose lookups the new index's leading
column still serves.

Both indexes are built and dropped ``CONCURRENTLY`` in autocommit blocks on
PostgreSQL so the migration never holds a write-blocking lock on ``events``.
The new index is built before the old one is dropped, so ``created_by``
lookups are always covered. On SQLite the concurrency flags are ignored and
the same plain indexes are created and dropped. Downgrade restores
``ix_events_created_by`` exactly as revision ``f5a6b7c8d9e0`` created it.

Revision ID: a1c3e5f7b9d2
Revises: c00de2f5d777
Create Date: 2026-10-17

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c3e5f7b9d2"  # pragma: allowlist secret
down_revision: str | None = "c00de2f5d777"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "events"
_NEW_INDEX = "ix_events_created_by_actor"
_OLD_INDEX = "ix_events_created_by"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _NEW_INDEX,
            _TABLE,
            ["created_by", "actor_id"],
            unique=False,
            postgresql_concurrently=True,
        )
    with op.get_context().autocommit_block():
        op.drop_index(_OLD_INDEX, table_name=_TABLE, postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _OLD_INDEX,
            _TABLE,
            ["created_by"],
            unique=False,
            postgresql_concurrently=True,
        )
    with op.get_context().autocommit_block():
        op.drop_index(_NEW_INDEX, table_name=_TABLE, postgresql_concurrently=True)
