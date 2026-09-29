"""scope the url index unique key to the revision

Revision ID: e7f8a9b0c1d2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-29

``operation_url_indexes`` used a global natural key
``(method, host, host_regex, path_template)``: one row per URL across every API
and every revision, overwritten by whichever revision was indexed last. A draft
import therefore replaced the live revision's rows, and archiving or deleting
the draft left the URL with no row at all.

This migration swaps that key for a per-revision one,
``(host, method, host_regex, path_template, revision_id)``, so every revision
keeps its own rows. Which rows serve an unpinned lookup is now decided at
query time (only each API's live revision — see ``UrlIndexRepository``).

The key is host-leading so the unpinned ``(host, method)`` lookup and the
promote-time host-ownership check are served by the unique index.

Schema only — the rows the old key already displaced are rebuilt by the
follow-up data migration ``e8f9a0b1c2d3``.

Downgrade restores the global key. Rows that would collide under it are
collapsed to one per URL first, preferring the row of the API's live
revision, then the most recently created row (the pre-change
last-writer-wins outcome).
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
_OLD_NAME = "uq_operation_url_index_lookup"
_OLD_COLUMNS = ["method", "host", "host_regex", "path_template"]
_NEW_NAME = "uq_operation_url_index_revision_lookup"
_NEW_COLUMNS = ["host", "method", "host_regex", "path_template", "revision_id"]

_URL_INDEX = sa.table(
    _TABLE,
    sa.column("id"),
    sa.column("revision_id"),
    sa.column("method"),
    sa.column("host"),
    sa.column("host_regex"),
    sa.column("path_template"),
    sa.column("created_at"),
)
_APIS = sa.table("apis", sa.column("current_revision_id"))


def _swap_constraint(drop_name: str, create_name: str, create_columns: list[str]) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(drop_name, _TABLE, type_="unique")
        op.create_unique_constraint(
            create_name, _TABLE, create_columns, postgresql_nulls_not_distinct=True
        )
    else:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(drop_name, type_="unique")
            batch.create_unique_constraint(create_name, create_columns)


def collapse_to_global_key(bind: sa.engine.Connection) -> None:
    """Keep one row per global URL key so the pre-change constraint can be restored.

    Preference: the row of an API's live revision, then the newest row.
    """
    live_ids = {
        row.current_revision_id
        for row in bind.execute(
            sa.select(_APIS.c.current_revision_id).where(_APIS.c.current_revision_id.is_not(None))
        )
    }
    rows = bind.execute(
        sa.select(
            _URL_INDEX.c.id,
            _URL_INDEX.c.revision_id,
            _URL_INDEX.c.method,
            _URL_INDEX.c.host,
            _URL_INDEX.c.host_regex,
            _URL_INDEX.c.path_template,
            _URL_INDEX.c.created_at,
        )
    ).all()

    groups: dict[tuple[object, ...], list[sa.Row[tuple[object, ...]]]] = {}
    for row in rows:
        key = (row.method, row.host, row.host_regex, row.path_template)
        groups.setdefault(key, []).append(row)

    doomed: list[object] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(
            key=lambda r: (r.revision_id in live_ids, r.created_at, str(r.id)),
            reverse=True,
        )
        doomed.extend(r.id for r in group[1:])

    if doomed:
        bind.execute(sa.delete(_URL_INDEX).where(_URL_INDEX.c.id.in_(doomed)))


def upgrade() -> None:
    _swap_constraint(_OLD_NAME, _NEW_NAME, _NEW_COLUMNS)


def downgrade() -> None:
    collapse_to_global_key(op.get_bind())
    _swap_constraint(_NEW_NAME, _OLD_NAME, _OLD_COLUMNS)
