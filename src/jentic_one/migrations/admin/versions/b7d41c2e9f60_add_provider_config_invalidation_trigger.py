"""add the cross-process invalidation NOTIFY trigger to provider_configs

Attaches the admin tree's ``jentic_one_notify_invalidation()`` (created by
``e046c1696c4b``) to ``provider_configs``: an ``AFTER INSERT OR UPDATE OR
DELETE`` row trigger plus an ``AFTER TRUNCATE`` statement trigger. Every
process that resolves credential providers listens for these notifications and
rebuilds its provider registry, so a provider config written through one
process reaches every other one without a restart.

The only key column reported is ``name``. ``config_json`` is never named as a
trigger argument, so the payload never carries a stored config — not even its
ciphertext. PostgreSQL only: SQLite has no notification channel.

Revision ID: b7d41c2e9f60
Revises: e046c1696c4b
Create Date: 2026-10-17

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d41c2e9f60"  # pragma: allowlist secret
down_revision: str | None = "e046c1696c4b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The trigger function ``e046c1696c4b`` creates in the admin schema.
FUNCTION = "jentic_one_notify_invalidation"

#: table -> key columns reported in the payload (never a secret-bearing column).
TRIGGERED_TABLES: dict[str, tuple[str, ...]] = {
    "provider_configs": ("name",),
}


def _row_trigger(table: str) -> str:
    return f"{table}_invalidation_row"


def _truncate_trigger(table: str) -> str:
    return f"{table}_invalidation_truncate"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, keys in TRIGGERED_TABLES.items():
        args = ", ".join(f"'{col}'" for col in keys)
        op.execute(
            f"CREATE TRIGGER {_row_trigger(table)} AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION {FUNCTION}({args})"
        )
        op.execute(
            f"CREATE TRIGGER {_truncate_trigger(table)} AFTER TRUNCATE "
            f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TRIGGERED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_truncate_trigger(table)} ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {_row_trigger(table)} ON {table}")
