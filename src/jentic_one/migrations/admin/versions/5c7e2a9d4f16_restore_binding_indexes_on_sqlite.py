"""restore binding-table indexes dropped by the SQLite rebuild

On SQLite, ``b9d0e1f2a3b4`` dropped the binding tables' ``agent_id`` FKs via
Alembic's batch mode, which rebuilds each table from a declared definition —
and that definition originally listed no indexes, so every named index on
``agent_credential_bindings`` and ``agent_toolkit_bindings`` was silently
dropped. ``b9d0e1f2a3b4`` now declares them (fresh databases keep them); this
revision repairs a SQLite database that already ran the original. Postgres
alters the table in place and never lost them.

``IF NOT EXISTS`` makes it a no-op wherever the indexes survived. The indexes
belong to the revisions that created them, so ``downgrade`` leaves them be.

Revision ID: 5c7e2a9d4f16
Revises: b9d0e1f2a3b4
Create Date: 2026-09-23

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5c7e2a9d4f16"  # pragma: allowlist secret
down_revision: str | None = "b9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_agent_credential_bindings_agent_id", "agent_credential_bindings", "agent_id"),
    ("ix_agent_credential_bindings_credential_id", "agent_credential_bindings", "credential_id"),
    ("ix_agent_credential_bindings_rule_set_id", "agent_credential_bindings", "rule_set_id"),
    ("ix_agent_credential_bindings_created_at", "agent_credential_bindings", "created_at"),
    ("ix_agent_credential_bindings_created_by", "agent_credential_bindings", "created_by"),
    ("ix_agent_toolkit_bindings_agent_id", "agent_toolkit_bindings", "agent_id"),
    ("ix_agent_toolkit_bindings_toolkit_id", "agent_toolkit_bindings", "toolkit_id"),
    ("ix_agent_toolkit_bindings_created_at", "agent_toolkit_bindings", "created_at"),
    ("ix_agent_toolkit_bindings_created_by", "agent_toolkit_bindings", "created_by"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column], if_not_exists=True)


def downgrade() -> None:
    pass
