"""Pin the raw-SQL agent purge sweep to the control schema it targets.

``AgentControlPurgeRepository`` reaches the control database with ``text()``
SQL because the auth module may not import the control ORM. A renamed table or
column there would only fail at purge time; this test fails at the rename.
"""

from __future__ import annotations

import pytest

# Registers every control table on ``ControlBase.metadata``.
import jentic_one.control.core.schema  # noqa: F401
from jentic_one.shared.db.base import ControlBase

#: The tables and columns the purge SQL names.
_PURGE_COLUMNS: dict[str, set[str]] = {
    "credentials": {"id", "state", "created_by"},
    "connect_sessions": {"credential_id", "agent_id", "initiator_actor_id"},
    "connect_session_outcomes": {"agent_id", "created_by"},
    "agent_permission_rules": {"agent_id"},
}


@pytest.mark.parametrize(("table", "columns"), sorted(_PURGE_COLUMNS.items()))
def test_purge_sql_names_existing_control_columns(table: str, columns: set[str]) -> None:
    assert table in ControlBase.metadata.tables
    assert columns <= set(ControlBase.metadata.tables[table].columns.keys())


def test_pending_session_credentials_cascade_to_their_session() -> None:
    """Deleting a session's pending credential must take the session row with it."""
    fks = ControlBase.metadata.tables["connect_sessions"].c.credential_id.foreign_keys
    assert [(fk.column.table.name, fk.ondelete) for fk in fks] == [("credentials", "CASCADE")]
