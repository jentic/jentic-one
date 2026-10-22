"""restore overlays wrongly deprecated by a cross-API supersede

Revision ID: e6858b8a3657
Revises: e8f9a0b1c2d3
Create Date: 2026-10-17

Data migration for #1078. An authorized catalog re-import that superseded a live
confirmed overlay auto-deprecated it (``deprecated_reason =
'superseded_by_reimport'``) even when the re-import landed on a *different* ``apis``
row from the overlay's (e.g. the clean identity of a vendor-doubled API). Nothing was
archived on the overlay's row, so its materialized revision is still the served
revision, yet the overlay reads DEPRECATED — a terminal state that blocks rollback and
leaves the next catalog re-import onto that row failing on
``ix_api_revisions_one_active``.

This restores exactly the rows only that bug can produce:

- ``status = 'deprecated'`` and ``deprecated_reason = 'superseded_by_reimport'``;
- ``confirmed_revision_id`` is an active (``published``/``imported``) revision of the
  overlay's own API. A legitimate supersede always archives the confirmed revision,
  so a legitimately superseded overlay never matches; rollback and manual deprecations
  carry a different reason;
- no other CONFIRMED overlay already claims that revision, so a row is never restored
  alongside an already-CONFIRMED overlay for that revision. Two deprecated candidates
  that share a revision are both restored, which reinstates their pre-#1078 state.

Matching rows go back to ``status = 'confirmed'`` with ``deprecated_at`` and
``deprecated_reason`` cleared. ``confirmed_at`` and the revision links are untouched.
Restored ids are logged. Idempotent: a restored row no longer matches, and a database
that never hit the bug is a no-op. Portable Core SQL (SQLite and Postgres).

Downgrade is a no-op: re-deprecating the rows would reinstate the bug, and once the
upgrade ran a restored overlay is indistinguishable from one that was never deprecated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import structlog
from alembic import op

from jentic_one.shared.db.types import GUID

revision: str = "e6858b8a3657"  # pragma: allowlist secret
down_revision: str | None = "e8f9a0b1c2d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = structlog.get_logger(__name__)

#: Overlay ids updated per statement.
_BATCH_SIZE = 500

# Frozen literals (not the live enums) so a later rename cannot change what this
# historical migration matches.
_DEPRECATED = "deprecated"
_CONFIRMED = "confirmed"
_SUPERSEDED_BY_REIMPORT = "superseded_by_reimport"
_ACTIVE_STATES = ("published", "imported")

_OVERLAYS = sa.table(
    "overlays",
    sa.column("id", sa.String()),
    sa.column("api_id", GUID()),
    sa.column("status", sa.String()),
    sa.column("confirmed_revision_id", GUID()),
    sa.column("deprecated_at", sa.DateTime()),
    sa.column("deprecated_reason", sa.String()),
    sa.column("updated_at", sa.DateTime()),
)
_REVISIONS = sa.table(
    "api_revisions",
    sa.column("id", GUID()),
    sa.column("api_id", GUID()),
    sa.column("state", sa.String()),
)


def wrongly_superseded_overlay_ids(bind: sa.engine.Connection) -> list[str]:
    """Ids of the overlays this migration restores (see module docstring)."""
    other = _OVERLAYS.alias("live_twin")
    still_served = (
        sa.exists()
        .where(
            _REVISIONS.c.id == _OVERLAYS.c.confirmed_revision_id,
            _REVISIONS.c.api_id == _OVERLAYS.c.api_id,
            _REVISIONS.c.state.in_(_ACTIVE_STATES),
        )
        .correlate(_OVERLAYS)
    )
    live_twin = (
        sa.exists()
        .where(
            other.c.confirmed_revision_id == _OVERLAYS.c.confirmed_revision_id,
            other.c.status == _CONFIRMED,
            other.c.id != _OVERLAYS.c.id,
        )
        .correlate(_OVERLAYS)
    )
    stmt = (
        sa.select(_OVERLAYS.c.id)
        .where(
            _OVERLAYS.c.status == _DEPRECATED,
            _OVERLAYS.c.deprecated_reason == _SUPERSEDED_BY_REIMPORT,
            _OVERLAYS.c.confirmed_revision_id.is_not(None),
            still_served,
            ~live_twin,
        )
        .order_by(_OVERLAYS.c.id)
    )
    return [row.id for row in bind.execute(stmt)]


def restore_wrongly_superseded_overlays(bind: sa.engine.Connection) -> list[str]:
    """Restore every matching overlay to CONFIRMED; returns the restored ids."""
    ids = wrongly_superseded_overlay_ids(bind)
    for start in range(0, len(ids), _BATCH_SIZE):
        batch = ids[start : start + _BATCH_SIZE]
        bind.execute(
            sa.update(_OVERLAYS)
            .where(
                _OVERLAYS.c.id.in_(batch),
                # Re-assert the state so a row that changed since the read is left alone.
                _OVERLAYS.c.status == _DEPRECATED,
                _OVERLAYS.c.deprecated_reason == _SUPERSEDED_BY_REIMPORT,
            )
            .values(
                status=_CONFIRMED,
                deprecated_at=None,
                deprecated_reason=None,
                updated_at=sa.func.now(),
            )
        )
    if ids:
        _logger.warning(
            "overlays_restored_from_wrong_supersede",
            count=len(ids),
            overlay_ids=ids,
            detail=(
                "confirmed overlays whose served revision a cross-API catalog re-import "
                "had marked deprecated (#1078)"
            ),
        )
    return ids


def upgrade() -> None:
    restore_wrongly_superseded_overlays(op.get_bind())


def downgrade() -> None:
    # Irreversible by design (see module docstring); nothing to undo.
    pass
