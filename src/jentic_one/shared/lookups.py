"""Cross-schema name resolution helpers.

These functions use raw SQL text queries to resolve human-readable names from
IDs that live in a different schema than the caller. This avoids cross-module
imports while keeping the resolution logic colocated.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.auth.identity import Identity


class CredentialCreatorLookupProtocol(Protocol):
    """Tell which actor ids created a credential the caller can read.

    Lets the admin actor lookup label the creator of a credential the caller
    can see without importing the control surface. The control surface
    implements it with its credential read scoping, extension-registered
    visibility (e.g. shares) included; the composition root installs it on
    the app as ``app.state.credential_creator_lookup``.
    """

    async def visible_creator_ids(
        self, actor_ids: Sequence[str], *, identity: Identity
    ) -> set[str]:
        """Return the subset of ``actor_ids`` that created a credential ``identity`` can read."""
        ...


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
