"""Cross-database lookup of the agents a user owns, for note read scoping.

A user may read the notes their own agents wrote (``registry/scoping/filters.py``).
Agents live in the **admin** database, so this repository resolves the owned
agent ids there and returns plain ids for
``build_access_filters(owned_agent_ids=...)``. It uses raw SQL (``text()``) so
the registry module never imports admin ORM models, the same boundary pattern
as ``governed_hosts_repo.py``, and runs against an admin-database session
handed in by the caller.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Archived agents are included: their notes stay readable to the owner.
_OWNED_AGENT_IDS = text("SELECT id FROM agents WHERE owner_id = :owner_id")


class AgentOwnershipBoundaryRepository:
    """Reads agent ownership from the admin DB without importing admin ORM."""

    @staticmethod
    async def list_agent_ids_owned_by(session: AsyncSession, *, owner_id: str) -> list[str]:
        """Return the ids of every agent whose ``owner_id`` is ``owner_id``."""
        result = await session.execute(_OWNED_AGENT_IDS, {"owner_id": owner_id})
        return [str(row[0]) for row in result.fetchall()]
