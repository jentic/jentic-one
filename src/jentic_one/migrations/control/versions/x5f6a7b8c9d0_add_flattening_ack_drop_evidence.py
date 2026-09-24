"""add Phase-6b drop evidence to toolkit_flattening_acks

Three columns the Phase-6b drop gates (control ``v3d4e5f6a7b8``, admin
``d1e2f3a4b5c6``) read before destroying the legacy toolkit tables:

- ``execution_names_backfilled``: the verification that wrote the row also
  confirmed every resolvable ``execution_records.toolkit_name`` was
  backfilled. Existing rows (written by a pre-6b tool, whose verification never
  checked it) default to false, so the drop refuses them — the operator
  re-runs ``flatten-toolkits`` and ``--verify --acknowledge`` on this release.
- ``control_state_digest`` / ``admin_state_digest``: which legacy rows the
  acknowledgement covered, so a stale ack (rows added or removed afterwards)
  is refused.

Its own revision, ahead of the drop, so that a ``migrations.run`` stopped by
the drop gate has already committed it: the Phase-6b tool can then write a
qualifying acknowledgement against the stopped schema. Idempotent.

Revision ID: x5f6a7b8c9d0
Revises: w4e5f6a7b8c9
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x5f6a7b8c9d0"  # pragma: allowlist secret
down_revision: str | None = "w4e5f6a7b8c9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "toolkit_flattening_acks"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    existing = _columns()
    with op.batch_alter_table(_TABLE) as batch_op:
        if "execution_names_backfilled" not in existing:
            batch_op.add_column(
                sa.Column(
                    "execution_names_backfilled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "control_state_digest" not in existing:
            batch_op.add_column(sa.Column("control_state_digest", sa.String(64), nullable=True))
        if "admin_state_digest" not in existing:
            batch_op.add_column(sa.Column("admin_state_digest", sa.String(64), nullable=True))


def downgrade() -> None:
    existing = _columns()
    with op.batch_alter_table(_TABLE) as batch_op:
        for name in ("admin_state_digest", "control_state_digest", "execution_names_backfilled"):
            if name in existing:
                batch_op.drop_column(name)
