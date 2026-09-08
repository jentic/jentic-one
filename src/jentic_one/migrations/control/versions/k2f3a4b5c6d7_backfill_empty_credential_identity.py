"""backfill empty-string credential identity axes to NULL

Credential ``api_name``/``api_version`` use ``NULL`` as the single "this axis is
unscoped (wildcard)" sentinel. Historically the create path stored an unset axis
as an empty string ``''`` instead, which matches neither ``NULL`` nor a concrete
operation identity — so a legitimately vendor-/vendor.name-scoped credential
silently stopped covering its operations and ``execute`` returned
``no_toolkit_binding`` (#775). The service layer now coerces empty→NULL on write;
this DML backfill fixes rows written before that.

DML-only (the columns are already nullable). Idempotent — re-running touches no
rows once the empty strings are gone.

Revision ID: k2f3a4b5c6d7
Revises: j1e2f3a4b5c6
Create Date: 2026-07-24

"""

from collections.abc import Sequence

from alembic import op

revision: str = "k2f3a4b5c6d7"
down_revision: str | None = "j1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE credentials SET api_name = NULL WHERE api_name = ''")
    op.execute("UPDATE credentials SET api_version = NULL WHERE api_version = ''")


def downgrade() -> None:
    # '' vs NULL is not meaningfully reversible: we cannot know which rows were
    # originally empty-string rather than genuinely unscoped. No-op by design.
    pass
