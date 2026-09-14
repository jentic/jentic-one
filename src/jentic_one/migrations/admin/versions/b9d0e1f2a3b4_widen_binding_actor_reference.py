"""widen binding actor references to any executing actor

Theme-5 Phase 4 (key retirement): ``jntc_live_`` toolkit-key holders migrate
to service accounts, which hold the same binding rows agents do — toolkit
bindings (``agent_toolkit_bindings``) so their broker access keeps deriving
through the toolkit path until the Phase-6a flattening, and direct bindings
(``agent_credential_bindings``) once flattened or bound via access requests.
Service accounts live in a sibling table (``service_accounts``), so both
``agent_id → agents.id`` FKs are dropped — the columns become loose actor
references (``agnt_…`` or ``sva_…``), matching the tables' other
cross-boundary columns (``toolkit_id``, ``credential_id``, ``rule_set_id``)
and the FK-less ``actor_scope_grants`` precedent. Row lifecycle stays
application-level: ``AgentService.delete`` already removes an agent's
bindings explicitly (it never relied on the CASCADE), and service accounts
archive rather than hard-delete.

Revision ID: b9d0e1f2a3b4
Revises: a7b8c9d0e1f2
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d0e1f2a3b4"  # pragma: allowlist secret
down_revision: str | None = "a7b8c9d0e1f2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAMES = {
    "agent_credential_bindings": "agent_credential_bindings_agent_id_fkey",
    "agent_toolkit_bindings": "agent_toolkit_bindings_agent_id_fkey",
}


def _credential_bindings_table(*, with_fk: bool) -> sa.Table:
    """``agent_credential_bindings`` as created by d9e0f1a2b3c4 (+ f1a2b3c4d5e6).

    SQLite cannot drop an unnamed FK in place; alembic batch mode rebuilds the
    table from this explicit definition. ``with_fk`` selects the pre-upgrade
    (FK present) or post-upgrade (FK absent) shape.
    """
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("credential_id", sa.String(30), nullable=False),
        sa.Column("rule_set_id", sa.String(30), nullable=True),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("suspended", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "agent_id", "credential_id", name="uq_agent_credential_bindings_agent_credential"
        ),
    ]
    if with_fk:
        columns.append(
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                ondelete="CASCADE",
                name=_FK_NAMES["agent_credential_bindings"],
            )
        )
    return sa.Table("agent_credential_bindings", sa.MetaData(), *columns)


def _toolkit_bindings_table(*, with_fk: bool) -> sa.Table:
    """``agent_toolkit_bindings`` as created by k0l1m2n3o4p5 (+ q6r7s8t9u0v1)."""
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("toolkit_id", sa.String(255), nullable=False),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "agent_id", "toolkit_id", name="uq_agent_toolkit_bindings_agent_toolkit"
        ),
    ]
    if with_fk:
        columns.append(
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
                ondelete="CASCADE",
                name=_FK_NAMES["agent_toolkit_bindings"],
            )
        )
    return sa.Table("agent_toolkit_bindings", sa.MetaData(), *columns)


_TABLE_TEMPLATES = {
    "agent_credential_bindings": _credential_bindings_table,
    "agent_toolkit_bindings": _toolkit_bindings_table,
}


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    for table, fk_name in _FK_NAMES.items():
        if pg:
            op.drop_constraint(fk_name, table, type_="foreignkey")
        else:
            template = _TABLE_TEMPLATES[table](with_fk=True)
            with op.batch_alter_table(table, copy_from=template) as batch:
                batch.drop_constraint(fk_name, type_="foreignkey")


def downgrade() -> None:
    """Re-tighten to agents-only.

    Fails (FK violation) if service-account bindings exist — delete the
    ``sva_…`` rows first; the guard is intentional (silently orphaning a
    migrated key-holder's access would be a data-plane outage, not a
    downgrade).
    """
    pg = op.get_bind().dialect.name == "postgresql"
    for table, fk_name in _FK_NAMES.items():
        if pg:
            op.create_foreign_key(
                fk_name, table, "agents", ["agent_id"], ["id"], ondelete="CASCADE"
            )
        else:
            template = _TABLE_TEMPLATES[table](with_fk=False)
            with op.batch_alter_table(table, copy_from=template) as batch:
                batch.create_foreign_key(
                    fk_name, "agents", ["agent_id"], ["id"], ondelete="CASCADE"
                )
