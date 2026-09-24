"""Data test for the retired access-request events acknowledgement migration.

Pins revision ``f1a2b3c4d5e7`` against a real (SQLite) database: an actionable,
unacknowledged ``access_request.filed`` row is closed with a retirement note,
while other actionable events and already-acknowledged rows are untouched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.migrations.run import upgrade

_DB = "admin"
_PARENT_REV = "b9d0e1f2a3b4"  # pragma: allowlist secret


@pytest.fixture
def sqlite_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config at fresh, empty per-database SQLite files."""
    cfg = tmp_path / "jentic-one.yaml"
    lines = ["databases:"]
    for name in ("admin", "control", "registry"):
        lines += [f"  {name}:", "    backend: sqlite", f"    path: {tmp_path / f'{name}.db'}"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg))
    return tmp_path


def _insert(conn: sqlite3.Connection, evt_id: str, type_: str, *, acknowledged: bool) -> None:
    conn.execute(
        "INSERT INTO events (id, type, severity, requires_action, acknowledged, summary, data,"
        " created_at) VALUES (?, ?, 'warning', 1, ?, 's', '{}', CURRENT_TIMESTAMP)",
        (evt_id, type_, 1 if acknowledged else 0),
    )


def test_acknowledges_only_open_access_request_events(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"
    upgrade(_DB, _PARENT_REV)
    with sqlite3.connect(db_path) as conn:
        _insert(conn, "evt_filed", "access_request.filed", acknowledged=False)
        _insert(conn, "evt_done", "access_request.filed", acknowledged=True)
        _insert(conn, "evt_other", "agent.registration_pending", acknowledged=False)

    upgrade(_DB)

    with sqlite3.connect(db_path) as conn:
        rows = {
            r[0]: r[1:]
            for r in conn.execute(
                "SELECT id, acknowledged, acknowledged_by, acknowledgement_note FROM events"
            )
        }
    assert rows["evt_filed"][0] == 1
    assert rows["evt_filed"][1] is None  # no human closed it
    assert "retired" in rows["evt_filed"][2]
    assert rows["evt_done"] == (1, None, None)
    assert rows["evt_other"] == (0, None, None)
