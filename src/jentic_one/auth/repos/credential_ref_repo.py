"""Cross-database credential lookup for the auth surface.

Uses raw SQL (``text()``) against the control database so the auth module
never imports the control ORM — the auth/control module boundary (enforced
by ``tests/arch/test_module_boundaries.py``) forbids a direct cross-module
import. Same convention as ``ToolkitNameRepository``.

Two consumers, both on the direct agent↔credential binding path (theme 5
phase 1): the bind route's visibility check (the caller must be able to see
the credential before binding an agent to it — the asymmetry the toolkit
bind route has is deliberately not carried over), and binding-list
enrichment (human-readable ``name`` plus the API the credential serves, the
credential-side analogue of the toolkit ``serves`` list from issue #686).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CredentialRef:
    """Primitive projection of a control-DB credential row for auth-side use."""

    id: str
    name: str
    created_by: str | None
    active: bool
    api_vendor: str
    api_name: str | None
    api_version: str | None


_COLUMNS = "id, name, created_by, active, api_vendor, api_name, api_version"


def _to_ref(row: Sequence[object]) -> CredentialRef:
    return CredentialRef(
        id=str(row[0]),
        name=str(row[1]),
        created_by=str(row[2]) if row[2] is not None else None,
        active=bool(row[3]),
        api_vendor=str(row[4]),
        api_name=str(row[5]) if row[5] is not None else None,
        api_version=str(row[6]) if row[6] is not None else None,
    )


class CredentialRefRepository:
    """Resolves credential ids to reference rows in the control DB without control imports."""

    @staticmethod
    async def get_by_id(session: AsyncSession, credential_id: str) -> CredentialRef | None:
        """Return the credential reference row, or ``None`` when it does not exist.

        Runs against a control-DB session. The caller applies its own
        visibility policy on the returned ``created_by``.
        """
        stmt = text(f"SELECT {_COLUMNS} FROM credentials WHERE id = :id")
        result = await session.execute(stmt, {"id": credential_id})
        row = result.fetchone()
        return _to_ref(row) if row is not None else None

    @staticmethod
    async def get_refs_for_ids(
        session: AsyncSession, credential_ids: Sequence[str]
    ) -> dict[str, CredentialRef]:
        """Return ``{credential_id: CredentialRef}`` for the given ids.

        Ids with no matching credential row (e.g. a since-deleted credential)
        are omitted, so callers get a reference only when one exists. Runs
        against a control-DB session; callers pass already scope-checked
        binding ids.
        """
        if not credential_ids:
            return {}
        unique_ids = list(dict.fromkeys(credential_ids))
        stmt = text(f"SELECT {_COLUMNS} FROM credentials WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        result = await session.execute(stmt, {"ids": unique_ids})
        return {str(row[0]): _to_ref(row) for row in result.fetchall()}
