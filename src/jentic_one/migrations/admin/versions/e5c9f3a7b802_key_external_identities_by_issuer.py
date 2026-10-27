"""add issuer to external_identities and key unique constraint on (issuer, external_subject)

External identities were previously keyed on ``(provider, external_subject)``.
Standard OIDC providers share ``provider="oidc"``, so switching the configured
``issuer`` to a different OIDC identity provider without changing ``provider``
could match an existing ``external_subject`` from the previous issuer.

This migration adds ``external_identities.issuer`` (``VARCHAR(255) NOT NULL
DEFAULT ''``), backfills existing rows (``https://accounts.google.com`` for
``provider = 'google'``, or ``provider`` for any other existing row), and
replaces ``uq_ext_id_provider_subject`` with ``uq_ext_id_issuer_subject`` on
``(issuer, external_subject)``.

Revision ID: e5c9f3a7b802
Revises: d4b8e2f6a901
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c9f3a7b802"  # pragma: allowlist secret
down_revision: str | None = "d4b8e2f6a901"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "external_identities"
_OLD_UQ = "uq_ext_id_provider_subject"
_NEW_UQ = "uq_ext_id_issuer_subject"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("issuer", sa.String(255), nullable=False, server_default=""),
    )
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET issuer = 'https://accounts.google.com' "
            "WHERE issuer = '' AND provider = 'google'"
        )
    )
    op.execute(sa.text(f"UPDATE {_TABLE} SET issuer = provider WHERE issuer = ''"))
    # Collapse any rows that collide on (issuer, external_subject) after backfill
    # before adding the issuer-scoped unique constraint.
    op.execute(
        sa.text(
            f"""
            DELETE FROM {_TABLE} WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY issuer, external_subject
                               ORDER BY created_at DESC, id DESC
                           ) AS rn
                    FROM {_TABLE}
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_OLD_UQ, type_="unique")
        batch_op.create_unique_constraint(_NEW_UQ, ["issuer", "external_subject"])


def downgrade() -> None:
    # Collapse any rows that share (provider, external_subject) across distinct
    # issuers before restoring the provider-scoped unique constraint.
    op.execute(
        sa.text(
            f"""
            DELETE FROM {_TABLE} WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY provider, external_subject
                               ORDER BY created_at DESC, id DESC
                           ) AS rn
                    FROM {_TABLE}
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_NEW_UQ, type_="unique")
        batch_op.create_unique_constraint(_OLD_UQ, ["provider", "external_subject"])
        batch_op.drop_column("issuer")
