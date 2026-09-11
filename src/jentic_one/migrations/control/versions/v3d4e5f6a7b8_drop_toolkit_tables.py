"""drop the four control toolkit tables (theme-5 phase 6b)

The deletion cut: ``toolkit_permission_rules``, ``toolkit_credential_bindings``,
``toolkit_keys``, ``toolkits`` are dropped in reverse-dependency order (the
same order the ``f7a8b9c0d1e2`` downgrade tears them down in). Access now
derives exclusively from direct agent↔credential bindings
(``agent_credential_bindings`` + ``agent_permission_rules`` /
``permission_rule_sets``), populated from these tables by the Phase-6a
flattening job.

Guard-and-raise (never guard-and-skip — Alembic would stamp the revision and
nothing would ever retry): the drop proceeds only when EITHER

- every doomed table in this database is empty (fresh installs and CI migrate
  empty schemas; dropping nothing is trivially safe), OR
- a ``toolkit_flattening_acks`` row exists — written only by
  ``jentic_one flatten-toolkits --verify --acknowledge`` after a passed
  verification, i.e. the operator has flattened the toolkit graph and
  explicitly signed off on the drop.

On PostgreSQL an additional ordering guard runs first: if any table outside
this set still holds a foreign key into ``toolkits`` (the enterprise
``toolkit_user_grants`` FK), the migration raises naming the enterprise
migration that must run first (``d47c3a91be02`` in jentic-one-enterprise
drops that FK and its table). Without the guard the DROP would fail with a
bare dependency error; with it the operator gets the runbook step. No-op on
SQLite (no cross-schema FKs exist there).

``downgrade()`` recreates the four tables **empty**, in their final
historical shape (post ``h9c0d1e2f3a4`` lookup_hash, ``m4a5b6c7d8e9``
match_mode, ``n5b6c7d8e9f0`` permissions-drop, ``t1b2c3d4e5f6``
migrated_actor_id, ``a8b9c0d1e2f3`` nullable created_by). Rows come back only
via ``jentic_one export-toolkits --import <file>`` from a pre-drop export —
see the rollback runbook in ``docs/releasing.md``.

Revision ID: v3d4e5f6a7b8
Revises: u2c3d4e5f6a7
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v3d4e5f6a7b8"  # pragma: allowlist secret
down_revision: str | None = "u2c3d4e5f6a7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Reverse-dependency drop order (children first), mirroring the
#: ``f7a8b9c0d1e2`` downgrade.
_DOOMED_TABLES = (
    "toolkit_permission_rules",
    "toolkit_credential_bindings",
    "toolkit_keys",
    "toolkits",
)

#: Foreign keys into ``toolkits`` from tables *outside* the doomed set. The
#: doomed children's own FKs are excluded — they drop with their tables; what
#: must not exist is an external consumer (the enterprise
#: ``toolkit_user_grants`` FK). ``to_regclass('toolkits')`` resolves through
#: the connection's search_path to this target's schema, so a same-named
#: table in another schema can never satisfy (or trip) the guard.
_FOREIGN_FK_QUERY = sa.text(
    "SELECT refn.nspname AS ref_schema, refc.relname AS ref_table, con.conname AS fk_name"
    " FROM pg_constraint con"
    " JOIN pg_class refc ON refc.oid = con.conrelid"
    " JOIN pg_namespace refn ON refn.oid = refc.relnamespace"
    " WHERE con.contype = 'f'"
    "   AND con.confrelid = to_regclass('toolkits')::oid"
    "   AND refc.relname NOT IN"
    "       ('toolkit_permission_rules', 'toolkit_credential_bindings', 'toolkit_keys')"
)

_REMEDIATION = (
    "Refusing to drop the toolkit tables: they still hold rows and no "
    "flattening acknowledgement is on record (toolkit_flattening_acks is "
    "empty). Run `jentic_one flatten-toolkits` (then re-run it to confirm "
    "zero creations), then `jentic_one flatten-toolkits --verify "
    "--acknowledge`, and re-run this migration. To discard the toolkit data "
    "instead, take an export first (`jentic_one export-toolkits --out "
    "<file>`), truncate the toolkit tables, and re-run. See the theme-5 "
    "upgrading runbook in docs/releasing.md."
)


def _count(bind: sa.engine.Connection, table: str) -> int:
    return int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


def _assert_no_foreign_fks(bind: sa.engine.Connection) -> None:
    """Ordering guard vs the enterprise overlay (PostgreSQL only)."""
    if bind.dialect.name != "postgresql":
        return
    rows = bind.execute(_FOREIGN_FK_QUERY).all()
    if rows:
        holders = ", ".join(f"{r.ref_schema}.{r.ref_table} ({r.fk_name})" for r in rows)
        raise RuntimeError(
            "Refusing to drop control.toolkits: foreign key(s) still reference "
            f"it from outside the toolkit tables: {holders}. Apply the "
            "enterprise migration first (jentic-one-enterprise d47c3a91be02 "
            "drops toolkit_user_grants and its FK), then re-run this "
            "migration. See the theme-5 upgrading runbook in docs/releasing.md."
        )


def _assert_gate(bind: sa.engine.Connection) -> None:
    """Guard-and-raise: empty tables OR an acknowledged flatten unblock the drop."""
    if all(_count(bind, table) == 0 for table in _DOOMED_TABLES):
        return
    if _count(bind, "toolkit_flattening_acks") > 0:
        return
    raise RuntimeError(_REMEDIATION)


def upgrade() -> None:
    bind = op.get_bind()
    _assert_no_foreign_fks(bind)
    _assert_gate(bind)

    # No explicit drop_index calls: both dialects drop a table's indexes with
    # the table, and index-by-name state can drift on SQLite (batch rebuilds
    # in earlier migrations discard indexes), so by-name drops are fragile.
    op.drop_table("toolkit_permission_rules")
    op.drop_table("toolkit_credential_bindings")
    op.drop_table("toolkit_keys")
    op.drop_table("toolkits")


def downgrade() -> None:
    """Recreate the four tables empty, in their final historical shape.

    A downgrade alone restores no access: rows come back only via
    ``jentic_one export-toolkits --import <file>`` (rollback = downgrade the
    drops **plus** re-import — docs/releasing.md). There are no ORM models
    for these tables any more, so raw inserts on SQLite must supply explicit
    ids (no server-side KSUID there), which the import tool does.
    """
    pg = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "toolkits",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("tk") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_toolkits_name"),
    )
    op.create_index("ix_toolkits_created_at", "toolkits", ["created_at"])
    op.create_index("ix_toolkits_created_by", "toolkits", ["created_by"])

    op.create_table(
        "toolkit_keys",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("ck") if pg else None,
            nullable=False,
        ),
        sa.Column("toolkit_id", sa.String(30), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column(
            "allowed_ips",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("revoked", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("key_preview", sa.String(50), nullable=False),
        sa.Column("hashed_key", sa.String(255), nullable=False),
        sa.Column("lookup_hash", sa.String(64), nullable=True),
        sa.Column("migrated_actor_id", sa.String(30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["toolkit_id"], ["toolkits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_toolkit_keys_toolkit_id", "toolkit_keys", ["toolkit_id"])
    op.create_index("ix_toolkit_keys_created_at", "toolkit_keys", ["created_at"])
    op.create_index("ix_toolkit_keys_created_by", "toolkit_keys", ["created_by"])
    op.create_index("ix_toolkit_keys_lookup_hash", "toolkit_keys", ["lookup_hash"], unique=True)

    op.create_table(
        "toolkit_credential_bindings",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("tcb") if pg else None,
            nullable=False,
        ),
        sa.Column("toolkit_id", sa.String(30), nullable=False),
        sa.Column("credential_id", sa.String(30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["toolkit_id"], ["toolkits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("toolkit_id", "credential_id", name="uq_toolkit_credential_binding"),
    )
    op.create_index(
        "ix_toolkit_credential_bindings_created_at",
        "toolkit_credential_bindings",
        ["created_at"],
    )
    op.create_index(
        "ix_toolkit_credential_bindings_created_by",
        "toolkit_credential_bindings",
        ["created_by"],
    )

    op.create_table(
        "toolkit_permission_rules",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("tpr") if pg else None,
            nullable=False,
        ),
        sa.Column("toolkit_id", sa.String(30), nullable=False),
        sa.Column("credential_id", sa.String(30), nullable=False),
        sa.Column("effect", sa.String(10), nullable=False),
        sa.Column(
            "methods",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("path", sa.String(1000), nullable=True),
        sa.Column("match_mode", sa.String(10), server_default="regex", nullable=False),
        sa.Column(
            "operations",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("is_system", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["toolkit_id"], ["toolkits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_toolkit_permission_rules_binding_seq",
        "toolkit_permission_rules",
        ["toolkit_id", "credential_id", "sequence"],
    )
    op.create_index(
        "ix_toolkit_permission_rules_created_at",
        "toolkit_permission_rules",
        ["created_at"],
    )
    op.create_index(
        "ix_toolkit_permission_rules_created_by",
        "toolkit_permission_rules",
        ["created_by"],
    )
