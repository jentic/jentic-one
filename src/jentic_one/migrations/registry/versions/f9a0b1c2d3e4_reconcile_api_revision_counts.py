"""reconcile apis.revision_count with the api_revisions rows

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-23

Data migration. A re-import whose digest matched a draft or archived revision
replaced that revision without giving its count back, and a revision delete
that raced such a replacement decremented a second time, so
``apis.revision_count`` drifted away from the real number of ``api_revisions``
rows (#1980). This sets every drifted count to the API's actual row count.
Rows whose count already matches are not written.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"  # pragma: allowlist secret
down_revision: str | None = "e8f9a0b1c2d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen, minimal table shapes: only the columns this migration reads or writes.
_APIS = sa.table("apis", sa.column("id"), sa.column("revision_count", sa.Integer()))
_API_REVISIONS = sa.table("api_revisions", sa.column("id"), sa.column("api_id"))


def reconcile_revision_counts(bind: sa.engine.Connection) -> int:
    """Set each drifted ``apis.revision_count`` to its row count; return rows updated.

    Idempotent: a second run finds nothing to update.
    """
    actual = (
        sa.select(sa.func.count(_API_REVISIONS.c.id))
        .where(_API_REVISIONS.c.api_id == _APIS.c.id)
        .scalar_subquery()
    )
    result = bind.execute(
        sa.update(_APIS).where(_APIS.c.revision_count != actual).values(revision_count=actual)
    )
    return result.rowcount


def upgrade() -> None:
    reconcile_revision_counts(op.get_bind())


def downgrade() -> None:
    # The drifted counts were wrong and cannot be reconstructed; the reconciled
    # counts are valid under every earlier revision.
    pass
