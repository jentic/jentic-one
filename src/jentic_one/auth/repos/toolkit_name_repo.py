"""Cross-database toolkit-name lookup for the auth surface.

Uses raw SQL (``text()``) against the control database so the auth module never
imports the control ORM — the auth/control module boundary (enforced by
``tests/arch/test_module_boundaries.py``) forbids a direct cross-module import.
This mirrors ``control.repos.prerequisite_repo.PrerequisiteRepository``, which
reaches the *admin* DB from the control side by the same raw-SQL convention.

The only consumer is the ``/me`` whoami assembly (issue #686): an agent can read
the *id* of a toolkit it is bound to but not its human-readable *name*, because
the binding row (admin DB) carries only ``toolkit_id`` while ``name`` lives in
the control DB ``toolkits`` table. Callers resolve names for a set of already
scope-checked binding ids, so this is read-only and scope-safe by construction —
it only ever maps ids the caller is already permitted to see.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


class ToolkitNameRepository:
    """Resolves toolkit ids to names in the control DB without control imports."""

    @staticmethod
    async def get_names_for_ids(
        session: AsyncSession, toolkit_ids: Sequence[str]
    ) -> dict[str, str]:
        """Return a ``{toolkit_id: name}`` map for the given ids.

        Ids with no matching toolkit row (e.g. a since-deleted toolkit) are
        omitted, so callers get a name only when one exists. Runs against a
        control-DB session.
        """
        if not toolkit_ids:
            return {}
        unique_ids = list(dict.fromkeys(toolkit_ids))
        stmt = text("SELECT id, name FROM toolkits WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        result = await session.execute(stmt, {"ids": unique_ids})
        return {row[0]: row[1] for row in result.fetchall()}
