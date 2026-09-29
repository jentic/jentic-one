"""scope the url index unique key to the revision

Revision ID: e7f8a9b0c1d2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-29

``operation_url_indexes`` used a global natural key
``(method, host, host_regex, path_template)``: one row per URL across every API
and every revision, overwritten by whichever revision was indexed last. A draft
import therefore replaced the live revision's rows, and archiving or deleting
the draft left the URL with no row at all.

This migration widens that key to a per-revision one,
``(host, method, host_regex, path_template, revision_id)``, so every revision
keeps its own rows. Which rows serve an unpinned lookup is now decided at
query time (only each API's live revision — see ``UrlIndexRepository``). The
key is host-leading so the unpinned ``(host, method)`` lookup and the
promote-time host-ownership check are served by the unique index.

Rolling-deploy compatibility
----------------------------
The Helm migrate Job runs ``pre-upgrade``, so pods of the previous release keep
serving against this schema until the Deployments finish rolling. Their ingest
upserts with ``ON CONFLICT ON CONSTRAINT uq_operation_url_index_lookup``, which
errors if no constraint of that name exists. The widened constraint therefore
**keeps the old name**: previous-release pods keep ingesting, and because their
statement now conflicts only within one revision they write exactly the rows
this release expects. There is no follow-up contract step for the write path.

Lock footprint (Postgres)
-------------------------
The new unique index is built ``CONCURRENTLY`` in an autocommit block, so reads
and writes continue while it builds. The swap itself (drop the old constraint,
attach the prebuilt index as the constraint under the same name) is a
catalog-only change inside the migration's transaction and holds its
``ACCESS EXCLUSIVE`` lock only briefly. A failed concurrent build leaves an
invalid index, which the next run drops before rebuilding. SQLite has no
concurrent DDL and rebuilds the table through ``batch_alter_table``.

Schema only — the rows the old key already displaced are rebuilt by the
follow-up data migration ``e8f9a0b1c2d3``.

Downgrade restores the global key. Rows that would collide under it are
collapsed to one per URL first, preferring the row of the API's live
revision, then the most recently created row (the pre-change
last-writer-wins outcome). The other revisions' rows for that URL are
deleted, which is the pre-change state (a pin on such a revision stops
resolving that URL, as it did before this migration).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"  # pragma: allowlist secret
down_revision: str | None = "e6f7a8b9c0d1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operation_url_indexes"
# Deliberately unchanged across the swap: previous-release pods name it in
# ``ON CONFLICT ON CONSTRAINT`` (see module docstring).
_NAME = "uq_operation_url_index_lookup"
_NEXT_INDEX = "uq_operation_url_index_lookup_next"
_OLD_COLUMNS = ["method", "host", "host_regex", "path_template"]
_NEW_COLUMNS = ["host", "method", "host_regex", "path_template", "revision_id"]


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _swap_sqlite(columns: list[str]) -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="unique")
        batch.create_unique_constraint(_NAME, columns)


def collapse_to_global_key(bind: sa.engine.Connection) -> None:
    """Keep one row per global URL key so the pre-change constraint can be restored.

    Preference: the row of an API's live revision, then the newest row. One
    set-based ``DELETE`` (window function, supported by Postgres and SQLite
    3.25+), so memory stays flat however large the index is. ``PARTITION BY``
    groups NULLs together, matching the key's NULLS NOT DISTINCT semantics.
    """
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {_TABLE} WHERE id IN (
                SELECT id FROM (
                    SELECT u.id AS id,
                           ROW_NUMBER() OVER (
                               PARTITION BY u.method, u.host, u.host_regex, u.path_template
                               ORDER BY
                                   CASE WHEN a.id IS NULL THEN 0 ELSE 1 END DESC,
                                   u.created_at DESC,
                                   u.id DESC
                           ) AS rn
                    FROM {_TABLE} u
                    LEFT JOIN apis a ON a.current_revision_id = u.revision_id
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )


def upgrade() -> None:
    if not _is_postgres():
        _swap_sqlite(_NEW_COLUMNS)
        return

    cols = ", ".join(_NEW_COLUMNS)
    with op.get_context().autocommit_block():
        # A previous failed run may have left an INVALID index behind.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_NEXT_INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {_NEXT_INDEX} "
            f"ON {_TABLE} ({cols}) NULLS NOT DISTINCT"
        )
    # Back in the migration's transaction: one atomic, catalog-only swap, so the
    # constraint name never disappears for a concurrent ``ON CONFLICT``.
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_NAME}")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_NAME} UNIQUE USING INDEX {_NEXT_INDEX}")


def downgrade() -> None:
    collapse_to_global_key(op.get_bind())
    if not _is_postgres():
        _swap_sqlite(_OLD_COLUMNS)
        return
    # Rollback path: an in-transaction rebuild keeps the collapse and the
    # re-keying atomic (no new colliding rows can land in between).
    op.drop_constraint(_NAME, _TABLE, type_="unique")
    op.create_unique_constraint(_NAME, _TABLE, _OLD_COLUMNS, postgresql_nulls_not_distinct=True)
