"""Client-side write guard for read-only sessions.

A session in ``AUTOCOMMIT`` has no transaction to roll back, so a write sent
through it is durable at once without any ``commit()``. The guard turns such a
write into an error before anything reaches the database.
"""

from __future__ import annotations

import re

from sqlalchemy import event
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.elements import ClauseElement, TextClause

READ_ONLY_WRITE_MESSAGE = "write attempted through read_session()"

# Leading verb of a textual DML statement, after any ``--`` / ``/* */``
# comments. Anchored to the verb so a read that merely mentions a keyword
# (``FOR UPDATE``, a ``'delete'`` literal, a keyword inside a comment) is not
# refused. Textual SQL is matched on its first verb only, so a data-modifying
# CTE in text (``WITH d AS (DELETE …) SELECT …``) is not detected.
_TEXTUAL_DML = re.compile(
    r"^\s*(?:(?:--[^\n]*(?:\n|$)|/\*.*?\*/)\s*)*(INSERT|UPDATE|DELETE|MERGE)\b",
    re.IGNORECASE | re.DOTALL,
)


def _contains_dml(statement: ClauseElement) -> bool:
    """Whether a Core/ORM statement embeds DML, e.g. ``insert(...).cte()``."""
    return any(isinstance(element, UpdateBase) for element in visitors.iterate(statement))


def install_read_only_guard(sess: AsyncSession) -> None:
    """Make ``sess`` raise ``InvalidRequestError`` on any write.

    Refuses an ORM flush with pending changes (including an autoflush before a
    query) and any ``INSERT``/``UPDATE``/``DELETE`` run through
    ``Session.execute``: ORM or Core DML, including DML nested in a CTE of a
    Core ``select``, or textual SQL whose first verb (after comments) is one
    of those (or ``MERGE``). Textual SQL is not parsed further, so DML inside a
    textual CTE is not detected. Listens on this session instance only, so
    other sessions are unaffected, and inspects the statement or the identity
    map client-side, so it sends nothing to the database.
    """
    sync_session = sess.sync_session

    def _refuse_flush(session: Session, _flush_context: object, _instances: object) -> None:
        if session.new or session.dirty or session.deleted:
            raise InvalidRequestError(READ_ONLY_WRITE_MESSAGE)

    def _refuse_dml(state: ORMExecuteState) -> None:
        if state.is_insert or state.is_update or state.is_delete:
            raise InvalidRequestError(READ_ONLY_WRITE_MESSAGE)
        statement = state.statement
        if isinstance(statement, TextClause):
            if _TEXTUAL_DML.search(statement.text):
                raise InvalidRequestError(READ_ONLY_WRITE_MESSAGE)
        elif isinstance(statement, ClauseElement) and _contains_dml(statement):
            raise InvalidRequestError(READ_ONLY_WRITE_MESSAGE)

    event.listen(sync_session, "before_flush", _refuse_flush)
    event.listen(sync_session, "do_orm_execute", _refuse_dml)
