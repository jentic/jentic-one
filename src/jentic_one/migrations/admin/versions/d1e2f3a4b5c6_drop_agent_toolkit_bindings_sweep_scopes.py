"""drop agent_toolkit_bindings + sweep retired toolkit scopes (theme-5 phase 6b)

The admin half of the deletion cut. Two pieces:

1. **Drop ``agent_toolkit_bindings``** — the legacy agent↔toolkit binding
   table. Guard-and-raise (never skip — Alembic would stamp the revision and
   nothing would retry): the drop proceeds only when EITHER the table is
   empty (fresh installs / CI), OR at least one direct binding row exists in
   ``agent_credential_bindings``. The Phase-6a acknowledgement sentinel lives
   in the **control** database, which this single-DB migration cannot see, so
   the admin gate is an in-DB proxy for "the flattening ran": every legacy
   ``(agent, credential)`` pair the flattening job derives lands as a direct
   binding, so legacy bindings alongside **zero** direct bindings means the
   flattening clearly has not run (or produced nothing — i.e. every legacy
   binding is dangling, which the operator must still resolve explicitly).
   The control-chain drop (``v3d4e5f6a7b8``) enforces the strict
   acknowledgement gate, and a full ``migrations.run`` invocation migrates
   control before admin, so this proxy is a second line, not the only line.
   Operators migrating ``--db admin`` in isolation get the same remediation
   message.

2. **Scope-data sweep** — the retired ``toolkits:read`` / ``toolkits:write``
   / ``owner:toolkits:read`` scope strings are purged from every stored
   grant/token surface: ``actor_scope_grants`` (one scope per row → rows
   deleted), ``user_permission_grants`` (same), and the array/string carriers
   ``access_tokens.scopes``, ``refresh_tokens.scopes``,
   ``oauth_client_grants.scopes``, ``oauth_clients.allowed_scopes`` (JSON
   arrays, rewritten minus the retired entries) and
   ``authorization_codes.scopes`` (space-separated string). Holding a retired
   scope has granted nothing since Phase 5b (``RETIRED_SCOPES`` is
   accept-and-ignore); this sweep removes the strings so the tolerance set
   itself can eventually retire. Array rewrites run in Python (dialect-safe
   for Postgres JSONB and SQLite JSON-as-TEXT alike), prefiltered by a
   LIKE probe so unaffected rows are never rewritten.

``downgrade()`` recreates ``agent_toolkit_bindings`` empty in its final
historical shape (post ``s8t9u0v1w2x3`` toolkit_id index, ``q6r7s8t9u0v1``
nullable created_by, ``b9d0e1f2a3b4`` FK-less agent_id). Rows come back only
via ``jentic_one export-toolkits --import <file>`` — see the rollback
runbook in ``docs/releasing.md``. The scope sweep is not reversed: the
strings granted nothing.

Revision ID: d1e2f3a4b5c6
Revises: c0e1f2a3b4c5
Create Date: 2026-09-11

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"  # pragma: allowlist secret
down_revision: str | None = "c0e1f2a3b4c5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Scope strings retired with the toolkit surface (mirrors
#: ``jentic_one.shared.scopes.RETIRED_SCOPES`` — copied, not imported:
#: migrations must stay runnable against the historical code state).
_RETIRED_SCOPES = frozenset({"toolkits:read", "toolkits:write", "owner:toolkits:read"})

#: (table, column) pairs storing one scope string per row → retired rows are
#: deleted outright.
_SCALAR_SCOPE_TABLES = (
    ("actor_scope_grants", "scope"),
    ("user_permission_grants", "permission"),
)

#: (table, column) pairs storing a JSON array of scope strings → arrays are
#: rewritten minus the retired entries.
_JSON_SCOPE_TABLES = (
    ("access_tokens", "scopes"),
    ("refresh_tokens", "scopes"),
    ("oauth_client_grants", "scopes"),
    ("oauth_clients", "allowed_scopes"),
)

_REMEDIATION = (
    "Refusing to drop agent_toolkit_bindings: it still holds rows and no "
    "direct binding rows exist in agent_credential_bindings, so the theme-5 "
    "Phase-6a flattening has clearly not run. Run `jentic_one "
    "flatten-toolkits` (then re-run it to confirm zero creations), then "
    "`jentic_one flatten-toolkits --verify --acknowledge`, and re-run this "
    "migration. To discard the binding data instead, take an export first "
    "(`jentic_one export-toolkits --out <file>`), truncate "
    "agent_toolkit_bindings, and re-run. See the theme-5 upgrading runbook "
    "in docs/releasing.md."
)


def _count(bind: sa.engine.Connection, table: str) -> int:
    return int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


def _assert_gate(bind: sa.engine.Connection) -> None:
    """Guard-and-raise: empty table OR evidence of a flattening run."""
    if _count(bind, "agent_toolkit_bindings") == 0:
        return
    if _count(bind, "agent_credential_bindings") > 0:
        return
    raise RuntimeError(_REMEDIATION)


def _parse_scopes(value: object) -> list[str] | None:
    """Coerce a raw JSON-array column value to a list (both dialects).

    Postgres JSONB arrives decoded (list); SQLite stores JSON as TEXT and a
    raw SELECT returns the string. ``None`` (nullable ``allowed_scopes``) and
    unparseable values pass through as ``None`` — nothing to sweep.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _sweep_scalar_tables(bind: sa.engine.Connection) -> None:
    for table, column in _SCALAR_SCOPE_TABLES:
        stmt = sa.text(f"DELETE FROM {table} WHERE {column} IN :retired").bindparams(
            sa.bindparam("retired", expanding=True)
        )
        bind.execute(stmt, {"retired": sorted(_RETIRED_SCOPES)})


def _sweep_json_tables(bind: sa.engine.Connection) -> None:
    pg = bind.dialect.name == "postgresql"
    for table, column in _JSON_SCOPE_TABLES:
        # Cheap prefilter: only rows whose serialized array can contain a
        # retired entry are read (every retired scope contains "toolkits").
        probe = f"CAST({column} AS TEXT)" if pg else column
        rows = bind.execute(
            sa.text(f"SELECT id, {column} AS scopes FROM {table} WHERE {probe} LIKE '%toolkits%'")
        ).all()
        assignment = f"{column} = CAST(:scopes AS JSONB)" if pg else f"{column} = :scopes"
        update = sa.text(f"UPDATE {table} SET {assignment} WHERE id = :id")
        for row in rows:
            scopes = _parse_scopes(row.scopes)
            if scopes is None:
                continue
            kept = [scope for scope in scopes if scope not in _RETIRED_SCOPES]
            if kept == scopes:
                continue
            bind.execute(update, {"id": row.id, "scopes": json.dumps(kept)})


def _sweep_authorization_codes(bind: sa.engine.Connection) -> None:
    """``authorization_codes.scopes`` is a space-separated string, not JSON."""
    rows = bind.execute(
        sa.text("SELECT id, scopes FROM authorization_codes WHERE scopes LIKE '%toolkits%'")
    ).all()
    update = sa.text("UPDATE authorization_codes SET scopes = :scopes WHERE id = :id")
    for row in rows:
        scopes = str(row.scopes or "").split()
        kept = [scope for scope in scopes if scope not in _RETIRED_SCOPES]
        if kept == scopes:
            continue
        bind.execute(update, {"id": row.id, "scopes": " ".join(kept)})


def upgrade() -> None:
    bind = op.get_bind()
    _assert_gate(bind)

    _sweep_scalar_tables(bind)
    _sweep_json_tables(bind)
    _sweep_authorization_codes(bind)

    # No explicit drop_index calls: both dialects drop a table's indexes with
    # the table, and on SQLite the ``b9d0e1f2a3b4`` batch rebuild already
    # discarded them (batch_alter_table does not carry indexes over), so a
    # by-name drop would raise "no such index" there.
    op.drop_table("agent_toolkit_bindings")


def downgrade() -> None:
    """Recreate ``agent_toolkit_bindings`` empty, in its final historical shape.

    Rows come back only via ``jentic_one export-toolkits --import <file>``
    (rollback = downgrade the drops **plus** re-import — docs/releasing.md).
    The scope sweep is not reversed. No ORM model exists for this table any
    more, so raw inserts on SQLite must supply explicit ids, which the import
    tool does.
    """
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "agent_toolkit_bindings",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("atb") if pg else None,
            nullable=False,
        ),
        # FK-less loose actor reference (agnt_… or sva_…) since b9d0e1f2a3b4.
        sa.Column("agent_id", sa.String(30), nullable=False),
        sa.Column("toolkit_id", sa.String(255), nullable=False),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
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
        sa.UniqueConstraint(
            "agent_id", "toolkit_id", name="uq_agent_toolkit_bindings_agent_toolkit"
        ),
    )
    op.create_index("ix_agent_toolkit_bindings_agent_id", "agent_toolkit_bindings", ["agent_id"])
    op.create_index(
        "ix_agent_toolkit_bindings_created_at", "agent_toolkit_bindings", ["created_at"]
    )
    op.create_index(
        "ix_agent_toolkit_bindings_created_by", "agent_toolkit_bindings", ["created_by"]
    )
    op.create_index(
        "ix_agent_toolkit_bindings_toolkit_id", "agent_toolkit_bindings", ["toolkit_id"]
    )
