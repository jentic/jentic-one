"""Data test for the wrongly-superseded overlay repair migration (#1078).

Pins revision ``e6858b8a3657`` against a real (SQLite) database through the real
Alembic chain: an overlay deprecated as ``superseded_by_reimport`` whose confirmed
revision is still the served revision of its own API is restored to CONFIRMED, while
legitimately superseded, rolled-back, manually deprecated, cross-API, unlinked and
already-claimed rows are untouched. Re-running the repair is a no-op and the
downgrade leaves the restored row alone.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import sqlalchemy as sa

from jentic_one.migrations.registry.versions import (
    e6858b8a3657_restore_overlays_wrongly_superseded as repair_migration,
)
from jentic_one.migrations.run import downgrade, upgrade

_DB = "registry"
_PARENT_REV = "f9a0b1c2d3e4"  # pragma: allowlist secret
_REV = "e6858b8a3657"  # pragma: allowlist secret

_REIMPORT = "superseded_by_reimport"


def _api(conn: sqlite3.Connection, name: str) -> str:
    api_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO apis (id, vendor, name, version) VALUES (?, 'example-com', ?, '1.0.0')",
        (api_id, name),
    )
    return api_id


def _revision(conn: sqlite3.Connection, api_id: str, state: str) -> str:
    rev_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO api_revisions (id, api_id, state) VALUES (?, ?, ?)", (rev_id, api_id, state)
    )
    return rev_id


def _overlay(
    conn: sqlite3.Connection,
    ovr_id: str,
    api_id: str,
    *,
    status: str = "deprecated",
    reason: str | None = _REIMPORT,
    confirmed_revision_id: str | None,
) -> None:
    conn.execute(
        "INSERT INTO overlays (id, api_id, status, document, confirmed_revision_id,"
        " confirmed_at, deprecated_at, deprecated_reason)"
        " VALUES (?, ?, ?, '{}', ?, CURRENT_TIMESTAMP, ?, ?)",
        (
            ovr_id,
            api_id,
            status,
            confirmed_revision_id,
            "2026-10-01 00:00:00" if status == "deprecated" else None,
            reason if status == "deprecated" else None,
        ),
    )


def _states(db_path: Path) -> dict[str, tuple[str, object, object]]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT id, status, deprecated_at, deprecated_reason FROM overlays"
            )
        }


def test_restores_only_overlays_whose_revision_is_still_served(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"
    upgrade(_DB, _PARENT_REV)
    with sqlite3.connect(db_path) as conn:
        doubled = _api(conn, "doubled")
        served = _revision(conn, doubled, "imported")
        archived = _revision(conn, doubled, "archived")
        other_api = _api(conn, "other")
        other_served = _revision(conn, other_api, "published")
        claimed_api = _api(conn, "claimed")
        claimed = _revision(conn, claimed_api, "imported")

        # The #1078 row: deprecated by a cross-API supersede, revision still served.
        _overlay(conn, "ovr_wrong", doubled, confirmed_revision_id=served)
        # A legitimate supersede archived the confirmed revision.
        _overlay(conn, "ovr_legit", doubled, confirmed_revision_id=archived)
        # Other deprecation causes never match, even over a served revision.
        _overlay(conn, "ovr_rollback", doubled, reason="rollback", confirmed_revision_id=served)
        _overlay(conn, "ovr_manual", doubled, reason="manual", confirmed_revision_id=served)
        _overlay(conn, "ovr_unknown", doubled, reason=None, confirmed_revision_id=served)
        # The served revision belongs to a different API row than the overlay's.
        _overlay(conn, "ovr_cross", doubled, confirmed_revision_id=other_served)
        # No linked revision: nothing proves it is still served.
        _overlay(conn, "ovr_unlinked", doubled, confirmed_revision_id=None)
        # A confirmed overlay already claims the served revision.
        _overlay(conn, "ovr_live", claimed_api, status="confirmed", confirmed_revision_id=claimed)
        _overlay(conn, "ovr_twin", claimed_api, confirmed_revision_id=claimed)
    before = _states(db_path)

    upgrade(_DB, _REV)

    after = _states(db_path)
    assert after["ovr_wrong"] == ("confirmed", None, None)
    assert {k: v for k, v in after.items() if k != "ovr_wrong"} == {
        k: v for k, v in before.items() if k != "ovr_wrong"
    }

    # Idempotent: the repair body finds nothing left to restore.
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            assert repair_migration.restore_wrongly_superseded_overlays(conn) == []
    finally:
        engine.dispose()
    assert _states(db_path) == after

    # Downgrade is a documented no-op: the restored overlay stays confirmed.
    downgrade(_DB, _PARENT_REV)
    assert _states(db_path) == after
