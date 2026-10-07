"""add lexical search_text column and full-text indexes

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f7
Create Date: 2026-07-01

Replaces the removed pgvector semantic-search columns with a lexical
full-text/BM25 search over an ``operations.search_text`` column.

- Drops the legacy ``search_embedding`` columns if they exist, so databases
  upgraded from the pgvector era are cleaned up. The ``vector`` extension is
  left in place: other tables may still depend on it, and dropping an
  extension needs its owner's privileges, which the migration role may lack.
- PostgreSQL: adds ``operations.search_text`` and a GIN expression index over
  ``to_tsvector('english', coalesce(search_text, ''))`` for ``ts_rank_cd``
  ranking via ``websearch_to_tsquery``.
- SQLite: adds ``operations.search_text``, an external ``operations_fts`` FTS5
  virtual table keyed by the operation id, and AFTER INSERT/UPDATE/DELETE
  triggers on ``operations`` that keep ``operations_fts`` synchronized.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"  # pragma: allowlist secret
down_revision: str | None = "a1b2c3d4e5f7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PG_FTS_INDEX = "ix_operations_search_text_fts"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Clean up legacy pgvector columns from databases upgraded from the
    # semantic-search era. Fresh installs never created them (the historical
    # migration is now a no-op), so guard each drop on existence.
    if _has_column("operations", "search_embedding"):
        op.drop_column("operations", "search_embedding")
    if _has_column("api_revisions", "search_embedding"):
        op.drop_column("api_revisions", "search_embedding")

    op.add_column("operations", sa.Column("search_text", sa.Text(), nullable=True))

    if dialect == "postgresql":
        op.execute(
            f"CREATE INDEX {_PG_FTS_INDEX} ON operations "
            "USING gin (to_tsvector('english', coalesce(search_text, '')))"
        )
    elif dialect == "sqlite":
        op.execute(
            "CREATE VIRTUAL TABLE operations_fts USING fts5("
            "op_id UNINDEXED, search_text, tokenize='porter unicode61')"
        )
        # Keep the FTS index in lockstep with the operations table. search_text
        # is set after insert (by the ingest stage), so both INSERT and UPDATE
        # upsert the FTS row; DELETE removes it.
        op.execute(
            """
            CREATE TRIGGER operations_ai AFTER INSERT ON operations BEGIN
                INSERT INTO operations_fts (op_id, search_text)
                VALUES (new.id, coalesce(new.search_text, ''));
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER operations_au AFTER UPDATE ON operations BEGIN
                DELETE FROM operations_fts WHERE op_id = old.id;
                INSERT INTO operations_fts (op_id, search_text)
                VALUES (new.id, coalesce(new.search_text, ''));
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER operations_ad AFTER DELETE ON operations BEGIN
                DELETE FROM operations_fts WHERE op_id = old.id;
            END
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {_PG_FTS_INDEX}")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS operations_ai")
        op.execute("DROP TRIGGER IF EXISTS operations_au")
        op.execute("DROP TRIGGER IF EXISTS operations_ad")
        op.execute("DROP TABLE IF EXISTS operations_fts")

    op.drop_column("operations", "search_text")
