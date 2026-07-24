"""re-slug legacy credential api_vendor/api_name to canonical form

Rows created before the credential service slugified the API identity on write
may store a non-canonical ``api_vendor``/``api_name`` (e.g. ``github.com`` rather
than ``github-com``), which can never intersect the slugified operation identity
the registry produces (#746). This narrow, idempotent backfill re-canonicalizes
only the rows whose value the slug transform would actually change.

Done in Python (not ``regexp_replace``) so it runs identically on Postgres and
SQLite. The slug rule is inlined as a frozen point-in-time copy: migrations must
not import evolving application code, and this must keep reproducing the rule as
it was when the migration was written even if the app helper later changes.

``api_version`` is intentionally left untouched — versions are trimmed, never
slugified (slugifying ``1.1.4`` → ``1-1-4`` would corrupt it).

DML-only. Idempotent — a canonical value slugifies to itself, so a re-run is a
no-op.

Revision ID: l3a4b5c6d7e8
Revises: k2f3a4b5c6d7
Create Date: 2026-07-24

"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l3a4b5c6d7e8"
down_revision: str | None = "k2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of shared.models.api_identity.slugify_api_field as of this revision.
# Do NOT import the app helper — migrations are point-in-time snapshots.
_API_FIELD_MAX_LENGTH = 100
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:_API_FIELD_MAX_LENGTH]


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, api_vendor, api_name FROM credentials")).fetchall()
    for row in rows:
        cred_id, vendor, name = row.id, row.api_vendor, row.api_name
        new_vendor = _slugify(vendor) if vendor else vendor
        new_name = _slugify(name) if name else name
        if new_vendor == vendor and new_name == name:
            continue
        bind.execute(
            sa.text("UPDATE credentials SET api_vendor = :vendor, api_name = :name WHERE id = :id"),
            {"vendor": new_vendor, "name": new_name, "id": cred_id},
        )


def downgrade() -> None:
    # Slug canonicalization is lossy (the original casing/punctuation is gone).
    # No-op by design.
    pass
