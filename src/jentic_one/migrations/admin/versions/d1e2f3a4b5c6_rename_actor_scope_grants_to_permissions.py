"""rename actor_scope_grants to actor_permission_grants

Aligns the grant table with the vocabulary the platform enforces on: a row here
is an internal authorization grant (a *permission*), not an OAuth2 scope. The
OAuth2 plane keeps its own names — ``access_tokens.scopes``,
``oauth_clients.allowed_scopes`` and ``oauth_client_grants.scopes`` are
untouched by this migration.

Pure rename of the table, its column, its unique constraint and its four
indexes. **No value migration:** rows keep their colon-form strings
(``agents:write`` stays ``agents:write``), and the ``asg`` KSUID prefix on
``id`` stays too, so existing ids remain valid. Fully reversible.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-09-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"  # pragma: allowlist secret
down_revision: str | None = "c0d1e2f3a4b5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TABLE = "actor_scope_grants"
_NEW_TABLE = "actor_permission_grants"
_OLD_UNIQUE = "uq_actor_scope_grants_actor_scope"
_NEW_UNIQUE = "uq_actor_permission_grants_actor_permission"
#: Postgres derived the primary key's name from the table it was created on, so
#: renaming the table leaves it behind. Nothing reads it — SQLAlchemy does not
#: name primary keys and autogenerate does not diff them — but it is the last
#: ``actor_scope_grants`` string in the schema, visible to anyone running ``\d``.
#: SQLite needs no equivalent: its batch rebuild emits an inline, unnamed
#: ``PRIMARY KEY (id)``.
_OLD_PKEY = "actor_scope_grants_pkey"
_NEW_PKEY = "actor_permission_grants_pkey"

#: Every index on the table, as ``(old_name, old_columns, new_name, new_columns)``.
#: Neither dialect renames an index when its table is renamed, so all four are
#: dropped and recreated here. The two ``created_*`` entries are the ones easy to
#: miss: they come from ``AuditableMixin``'s ``index=True`` columns, whose names
#: alembic derives from ``__tablename__`` — leaving them under the old table's
#: name would surface as autogenerate drift rather than a runtime failure.
_INDEXES: tuple[tuple[str, list[str], str, list[str]], ...] = (
    (
        "ix_actor_scope_grants_scope",
        ["scope"],
        "ix_actor_permission_grants_permission",
        ["permission"],
    ),
    (
        "ix_actor_scope_grants_actor",
        ["actor_id", "actor_type"],
        "ix_actor_permission_grants_actor",
        ["actor_id", "actor_type"],
    ),
    (
        "ix_actor_scope_grants_created_at",
        ["created_at"],
        "ix_actor_permission_grants_created_at",
        ["created_at"],
    ),
    (
        "ix_actor_scope_grants_created_by",
        ["created_by"],
        "ix_actor_permission_grants_created_by",
        ["created_by"],
    ),
)


def _rename_column_and_constraints(
    *,
    table: str,
    old_column: str,
    new_column: str,
    old_unique: str,
    new_unique: str,
    unique_columns: list[str],
    old_pkey: str,
    new_pkey: str,
) -> None:
    """Rename one column and re-name the constraints tied to the old table name.

    Postgres does all of it in place. SQLite has no ``ALTER`` for any of it, so
    alembic's batch mode rebuilds the table — and the column rename and the unique
    swap must sit in **separate** batch blocks: a constraint created in the same
    block that renames the column it spans is silently dropped on the rebuild,
    which would take the ``ON CONFLICT (actor_id, permission)`` upsert in
    ``EffectsRepository.grant_permission_to_actor`` with it.
    """
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(table, old_column, new_column_name=new_column)
        op.drop_constraint(old_unique, table, type_="unique")
        op.create_unique_constraint(new_unique, table, unique_columns)
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old_pkey} TO {new_pkey}")
        return

    with op.batch_alter_table(table) as batch:
        batch.alter_column(
            old_column,
            new_column_name=new_column,
            existing_type=sa.String(64),
            existing_nullable=False,
        )
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(old_unique, type_="unique")
    with op.batch_alter_table(table) as batch:
        batch.create_unique_constraint(new_unique, unique_columns)


def upgrade() -> None:
    for old_name, _old_columns, _new_name, _new_columns in _INDEXES:
        op.drop_index(old_name, table_name=_OLD_TABLE)

    op.rename_table(_OLD_TABLE, _NEW_TABLE)

    _rename_column_and_constraints(
        table=_NEW_TABLE,
        old_column="scope",
        new_column="permission",
        old_unique=_OLD_UNIQUE,
        new_unique=_NEW_UNIQUE,
        unique_columns=["actor_id", "permission"],
        old_pkey=_OLD_PKEY,
        new_pkey=_NEW_PKEY,
    )

    for _old_name, _old_columns, new_name, new_columns in _INDEXES:
        op.create_index(new_name, _NEW_TABLE, new_columns)


def downgrade() -> None:
    for _old_name, _old_columns, new_name, _new_columns in _INDEXES:
        op.drop_index(new_name, table_name=_NEW_TABLE)

    op.rename_table(_NEW_TABLE, _OLD_TABLE)

    _rename_column_and_constraints(
        table=_OLD_TABLE,
        old_column="permission",
        new_column="scope",
        old_unique=_NEW_UNIQUE,
        new_unique=_OLD_UNIQUE,
        unique_columns=["actor_id", "scope"],
        old_pkey=_NEW_PKEY,
        new_pkey=_OLD_PKEY,
    )

    for old_name, old_columns, _new_name, _new_columns in _INDEXES:
        op.create_index(old_name, _OLD_TABLE, old_columns)
