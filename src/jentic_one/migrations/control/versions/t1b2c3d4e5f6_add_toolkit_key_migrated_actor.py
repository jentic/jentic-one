"""toolkit_keys: record the service account each key retired to

Theme-5 Phase 4 (key retirement): the migration job converts each resolvable
``jntc_live_`` key into a service account, and the presented plaintext keeps
authenticating — as that service account — through the unified
``ApiKeyResolver`` for the deprecation window. ``migrated_actor_id`` is the
job's stamp (``sva_…``, cross-DB FK-less reference into the admin database):
it makes the job idempotent (stamped keys are skipped on re-run) and keeps
the legacy revoke surface honest — revoking a migrated key must also disable
the service account it retired to, or the "revoked" key would keep executing.

Revision ID: t1b2c3d4e5f6
Revises: s0a1b2c3d4e5
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t1b2c3d4e5f6"  # pragma: allowlist secret
down_revision: str | None = "s0a1b2c3d4e5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "toolkit_keys",
        sa.Column("migrated_actor_id", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("toolkit_keys", "migrated_actor_id")
