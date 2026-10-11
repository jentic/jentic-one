"""Every ORM timestamp column reads back timezone-aware, on every backend.

SQLite has no real timestamp type: a plain ``DateTime(timezone=True)`` column
round-trips as a naive ``datetime``. An API response then carries an instant
with no offset (``2026-10-11T10:17:59``), which strict clients refuse — the Go
CLI's ``time.Time`` decode fails on ``/me`` once an agent has a binding. The
``UTCDateTime`` decorator tags naive values as UTC in both directions, so each
model column uses it. Postgres DDL is the same either way (``timestamptz``).
"""

from __future__ import annotations

from sqlalchemy import DateTime

from jentic_one.migrations.targets import DB_TARGETS
from jentic_one.shared.db.types import UTCDateTime


def test_every_timestamp_column_uses_utc_datetime() -> None:
    offenders = [
        f"{name}.{table.name}.{column.name}"
        for name, target in DB_TARGETS.items()
        for table in target.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime) and not isinstance(column.type, UTCDateTime)
    ]
    assert offenders == [], (
        "use UTCDateTime() rather than DateTime(...) so SQLite reads it back "
        f"timezone-aware: {offenders}"
    )
