"""add catalog_api_id + display_name to oauth_app_registrations

Admin-registered OAuth apps are now fully independent of the platform
``AppConfig.vendors.entries`` snapshot. Two fields the admin picks at
registration time replace the previous config-merge behaviour:

* ``catalog_api_id`` — the catalog API slug the OAuth app targets (e.g.
  ``googleapis-com/gmail``). Used verbatim as the credential's
  ``catalog_api_id`` at connect time so the operations preview on the
  rules page resolves against a real registered API instead of a
  synthesized ``{slug}/{slug}`` placeholder.
* ``display_name`` — vendor family label (``Gmail``), shown alongside the
  admin's per-registration ``name`` on the picker card.

Both nullable so any admin registrations created on this branch before
the refactor keep loading; the create endpoint requires them for all new
rows.

Revision ID: cc3d4e5f6a7b
Revises: bb2c3d4e5f6a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cc3d4e5f6a7b"  # pragma: allowlist secret
down_revision: str | None = "bb2c3d4e5f6a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oauth_app_registrations",
        sa.Column("catalog_api_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "oauth_app_registrations",
        sa.Column("display_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_app_registrations", "display_name")
    op.drop_column("oauth_app_registrations", "catalog_api_id")
