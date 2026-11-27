"""add credential grant expiry

How an agent's access to a credential (its binding) ends on its own:

- ``agent_credential_bindings``:
  - ``expires_at``: the end date (``NULL``: none);
  - ``max_idle_period``: the idle limit in whole seconds (``NULL``: none); the
    binding ends once it goes this long without a use. The API carries it as
    an ISO 8601 duration; storage keeps seconds so SQL can compare it;
  - ``last_used_at``: the agent's last call through the binding (stamped at
    most about hourly), backfilled from the latest execution record;
  - ``renewed_at``: when it was granted or last renewed (backfilled from
    ``bound_at``); the idle clock runs from the later of this and
    ``last_used_at``;
  - ``max_quota`` / ``quota_remaining``: a use count (``NULL``: unlimited);
  - ``idle_warning``: the owner's opt-in warning before the binding ends, with
    ``idle_warned_at`` / ``expiry_warned_at`` marking each warning sent;
  - ``after_expiry``: ``hold`` (default: the first call after the binding
    ends is held for renewal) or ``refuse`` (a plain 403);
- ``held_executions.kind``: ``execution`` (an Ask rule) or ``grant_renewal``
  (a call held because its binding ended), with a partial unique index
  keeping one pending renewal per binding;
- ``ix_execution_records_actor_credential_last_used``: answers "last used" per
  agent, credential and API from the index alone.

Nothing here limits existing access: every existing binding gets no limit,
and every warning is off.

Locks: the index is built ``CONCURRENTLY`` in an autocommit block first (a
failed earlier build's INVALID leftover is dropped). The latest use of each
binding is aggregated from ``execution_records`` before any column is added,
since adding one locks ``agent_credential_bindings`` (read by the broker on
every call) until the migration commits; the backfill then writes by primary
key in batches.

The downgrade fails closed. Older code ignores every limit, so before the
columns go: every binding carrying an end date, idle limit or use count is
suspended, as ``binding_expired`` when a limit has been reached (relabelling
one already suspended) and ``binding_policy_removed`` otherwise. An operator
resumes each knowingly. A pending renewal
hold left behind reads as an ordinary held call, and approving it runs into
the still-suspended binding, which the run-time check refuses.

Revision ID: a3f7c1e9d5b2
Revises: 3ff5fd639357
Create Date: 2026-11-24

"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c1e9d5b2"  # pragma: allowlist secret
down_revision: str | None = "3ff5fd639357"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LAST_USED_INDEX = "ix_execution_records_actor_credential_last_used"
_PENDING_RENEWAL_INDEX = "uq_held_executions_pending_renewal"
_PENDING_RENEWAL = "state = 'pending' AND kind = 'grant_renewal'"
_BATCH = 500

_BINDINGS = sa.table(
    "agent_credential_bindings",
    sa.column("id", sa.String),
    sa.column("agent_id", sa.String),
    sa.column("credential_id", sa.String),
    sa.column("bound_at", sa.DateTime(timezone=True)),
    sa.column("suspended", sa.Boolean),
    sa.column("suspended_reason", sa.String),
    sa.column("expires_at", sa.DateTime(timezone=True)),
    sa.column("max_idle_period", sa.Integer),
    sa.column("last_used_at", sa.DateTime(timezone=True)),
    sa.column("renewed_at", sa.DateTime(timezone=True)),
    sa.column("max_quota", sa.Integer),
    sa.column("quota_remaining", sa.Integer),
)
# Untyped columns for the batched write: each value goes back exactly as the
# aggregate read it, so SQLite keeps the execution record's stored format.
_BACKFILL = sa.table("agent_credential_bindings", sa.column("id"), sa.column("last_used_at"))
_RECORDS = sa.table(
    "execution_records",
    sa.column("actor_id", sa.String),
    sa.column("credential_id", sa.String),
    sa.column("started_at", sa.DateTime(timezone=True)),
)


def _binding_columns() -> tuple[sa.Column[Any], ...]:
    """Fresh column objects (a Column belongs to one table once added)."""
    return (
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_idle_period", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_warning", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("idle_warned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_warned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_quota", sa.Integer(), nullable=True),
        sa.Column("quota_remaining", sa.Integer(), nullable=True),
        sa.Column("after_expiry", sa.String(16), nullable=False, server_default=sa.text("'hold'")),
    )


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


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
        op.drop_index(
            index_name, table_name="execution_records", postgresql_concurrently=True, if_exists=True
        )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _drop_invalid_leftover(_LAST_USED_INDEX)
        op.create_index(
            _LAST_USED_INDEX,
            "execution_records",
            ["actor_id", "credential_id", "api_vendor", "api_name", "started_at"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    conn = op.get_bind()
    # The one slow read, before any DDL: adding a column or index takes a lock that
    # lasts until the migration commits, so a long scan of the execution history
    # must not run after it and stall the table's readers and writers meanwhile.
    existing = _columns("agent_credential_bindings")
    last_used: list[dict[str, object]] = []
    if "last_used_at" not in existing:
        # Before the bindings table is locked: one aggregate over the records.
        last_used = [
            {"b_id": row[0], "b_last_used": row[1]}
            for row in conn.execute(
                sa.select(_BINDINGS.c.id, sa.func.max(_RECORDS.c.started_at))
                .select_from(
                    _BINDINGS.join(
                        _RECORDS,
                        sa.and_(
                            _RECORDS.c.actor_id == _BINDINGS.c.agent_id,
                            _RECORDS.c.credential_id == _BINDINGS.c.credential_id,
                        ),
                    )
                )
                .group_by(_BINDINGS.c.id)
            )
        ]
    if "kind" not in _columns("held_executions"):
        op.add_column(
            "held_executions",
            sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'execution'")),
        )
    if _PENDING_RENEWAL_INDEX not in {
        ix["name"] for ix in sa.inspect(conn).get_indexes("held_executions")
    }:
        op.create_index(
            _PENDING_RENEWAL_INDEX,
            "held_executions",
            ["agent_id", "credential_id"],
            unique=True,
            postgresql_where=sa.text(_PENDING_RENEWAL),
            sqlite_where=sa.text(_PENDING_RENEWAL),
        )

    for column in _binding_columns():
        if column.name not in existing:
            op.add_column("agent_credential_bindings", column)
    write = (
        _BACKFILL.update()
        .where(_BACKFILL.c.id == sa.bindparam("b_id"))
        .values(last_used_at=sa.bindparam("b_last_used"))
    )
    for start in range(0, len(last_used), _BATCH):
        conn.execute(write, last_used[start : start + _BATCH])
    if "renewed_at" not in existing:
        op.execute(_BINDINGS.update().values(renewed_at=_BINDINGS.c.bound_at))


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ended(row: Any, now: datetime) -> bool:
    """Whether any of the binding's limits has been reached (compared in Python: SQLite)."""
    if row.expires_at is not None and _as_utc(row.expires_at) <= now:
        return True
    if row.max_idle_period is not None:
        starts = [_as_utc(v) for v in (row.last_used_at, row.renewed_at or row.bound_at) if v]
        if starts and max(starts) + timedelta(seconds=row.max_idle_period) <= now:
            return True
    return row.max_quota is not None and (row.quota_remaining or 0) <= 0


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC)

    limited = conn.execute(
        sa.select(
            _BINDINGS.c.id,
            _BINDINGS.c.suspended,
            _BINDINGS.c.bound_at,
            _BINDINGS.c.expires_at,
            _BINDINGS.c.max_idle_period,
            _BINDINGS.c.last_used_at,
            _BINDINGS.c.renewed_at,
            _BINDINGS.c.max_quota,
            _BINDINGS.c.quota_remaining,
        ).where(
            sa.or_(
                _BINDINGS.c.expires_at.is_not(None),
                _BINDINGS.c.max_idle_period.is_not(None),
                _BINDINGS.c.max_quota.is_not(None),
            )
        )
    ).all()
    ended = {row.id for row in limited if _ended(row, now)}
    for bindings, reason in (
        (ended, "binding_expired"),
        ({row.id for row in limited if not row.suspended} - ended, "binding_policy_removed"),
    ):
        if bindings:
            conn.execute(
                _BINDINGS.update()
                .where(_BINDINGS.c.id.in_(sorted(bindings)))
                .values(suspended=True, suspended_reason=reason)
            )
    for column in reversed(_binding_columns()):
        op.drop_column("agent_credential_bindings", column.name)

    op.drop_index(_PENDING_RENEWAL_INDEX, table_name="held_executions")
    op.drop_column("held_executions", "kind")

    with op.get_context().autocommit_block():
        op.drop_index(
            _LAST_USED_INDEX,
            table_name="execution_records",
            postgresql_concurrently=True,
            if_exists=True,
        )
