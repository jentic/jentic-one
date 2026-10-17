"""Cross-schema name resolution helpers.

These functions use raw SQL text queries to resolve human-readable names from
IDs that live in a different schema than the caller. This avoids cross-module
imports while keeping the resolution logic colocated.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_credential_names(session: AsyncSession, ids: list[str]) -> dict[str, str]:
    """Resolve credential IDs to names via the control schema.

    Returns a mapping of {credential_id: name} for IDs that exist.
    Missing IDs are omitted from the result.
    """
    if not ids:
        return {}
    stmt = text("SELECT id, name FROM credentials WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    result = await session.execute(stmt, {"ids": ids})
    return {row.id: row.name for row in result}
