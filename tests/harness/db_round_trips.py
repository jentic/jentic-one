"""Record what a block of code sends to one database, for round-trip assertions.

Captures every cursor statement (``before_cursor_execute``), SQLAlchemy's
logical ``begin``/``commit``/``rollback`` events, and — for each statement —
whether the *driver* connection was inside a transaction when it ran.

SQLAlchemy fires ``begin``/``rollback`` for every connection use, including in
autocommit, where the driver turns them into no-ops. So "no BEGIN/ROLLBACK on
the wire" is asserted as: no transaction-control statement was executed, every
``begin``/``rollback`` event fired on a connection in driver autocommit, and the
driver was never inside a transaction while a statement ran. asyncpg issues its
``BEGIN`` outside the cursor, so the driver-side check is what catches it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event

from jentic_one.shared.db.session import DatabaseSession

_TX_CONTROL = re.compile(r"^\s*(BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\b", re.IGNORECASE)


@dataclass
class RoundTrips:
    """What one traced block sent to the database."""

    statements: list[str] = field(default_factory=list)
    #: ``(event, driver_autocommit)`` for each ``begin``/``commit``/``rollback``.
    tx_events: list[tuple[str, bool]] = field(default_factory=list)
    #: Driver-side "inside a transaction" flag, one per statement, after it ran.
    in_transaction: list[bool] = field(default_factory=list)

    @property
    def tx_control_statements(self) -> list[str]:
        return [s for s in self.statements if _TX_CONTROL.match(s)]

    def assert_no_begin_or_rollback(self) -> None:
        """Fail unless the block ran without any transaction on the wire."""
        assert self.statements, "no statement was traced"
        assert not self.tx_control_statements, self.statements
        assert all(autocommit for _, autocommit in self.tx_events), self.tx_events
        assert not any(self.in_transaction), (self.statements, self.in_transaction)


def _driver_autocommit(conn: Any) -> bool:
    return bool(conn.dialect.detect_autocommit_setting(conn.connection.dbapi_connection))


def _driver_in_transaction(conn: Any) -> bool:
    driver = conn.connection.driver_connection
    probe = getattr(driver, "is_in_transaction", None)  # asyncpg
    if callable(probe):
        return bool(probe())
    return bool(driver.in_transaction)  # sqlite3 / aiosqlite


@contextmanager
def trace_round_trips(db: DatabaseSession) -> Iterator[RoundTrips]:
    """Record the statements and transaction events ``db``'s engine sees."""
    trace = RoundTrips()
    engine = db.engine.sync_engine

    def _before(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        trace.statements.append(statement)

    def _after(conn: Any, *_rest: Any) -> None:
        trace.in_transaction.append(_driver_in_transaction(conn))

    def _tx(name: str) -> Any:
        def _handler(conn: Any) -> None:
            trace.tx_events.append((name, _driver_autocommit(conn)))

        return _handler

    listeners: list[tuple[str, Any]] = [
        ("before_cursor_execute", _before),
        ("after_cursor_execute", _after),
        *((name, _tx(name)) for name in ("begin", "commit", "rollback")),
    ]
    for name, fn in listeners:
        event.listen(engine, name, fn)
    try:
        yield trace
    finally:
        for name, fn in listeners:
            event.remove(engine, name, fn)
