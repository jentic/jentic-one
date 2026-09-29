"""Cross-database suspension of agent credential bindings on API delete (#1168).

Deleting an API deactivates the control credentials stored for it
(``control_credential_boundary_repo.py``, #643), but the agent bindings to
those credentials live in the **admin** database. Left alone they stay
active, so re-activating the credential, or re-importing a spec under the
same ``(vendor, name, version)``, would silently hand the bindings and their
permission rules back to every agent.

This repository suspends those bindings with reason ``api_deleted``.
Suspension (not deletion) keeps the binding row and its authored permission
rules, so an owner can restore access deliberately with the existing
``:resume`` action. It uses raw SQL (``text()``) so the registry module never
imports admin ORM models, the same boundary pattern as
``governed_hosts_repo.py``. It runs against an **admin-database** session
handed in by the caller and is flush-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

SUSPENDED_REASON_API_DELETED = "api_deleted"

# Only bindings that are not already suspended change: a binding an owner
# suspended by hand keeps its (NULL) reason, so resuming it later behaves the
# same as before the API was deleted.
_SUSPEND_BINDINGS = text(
    "UPDATE agent_credential_bindings "
    "SET suspended = true, suspended_reason = :reason "
    "WHERE credential_id IN :credential_ids AND suspended = false "
    "RETURNING id, agent_id, credential_id"
).bindparams(bindparam("credential_ids", expanding=True))


@dataclass(frozen=True)
class SuspendedBinding:
    """One agent binding suspended by the API delete."""

    id: str
    agent_id: str
    credential_id: str


class AdminCredentialBindingBoundaryRepository:
    """Suspends admin-DB agent bindings to a set of control credentials."""

    @staticmethod
    async def suspend_bindings_for_credentials(
        session: AsyncSession, *, credential_ids: list[str], reason: str
    ) -> list[SuspendedBinding]:
        """Suspend every active binding to the credentials; return the rows changed."""
        if not credential_ids:
            return []
        rows = (
            await session.execute(
                _SUSPEND_BINDINGS, {"credential_ids": sorted(credential_ids), "reason": reason}
            )
        ).all()
        await session.flush()
        suspended = [
            SuspendedBinding(id=row.id, agent_id=row.agent_id, credential_id=row.credential_id)
            for row in rows
        ]
        return sorted(suspended, key=lambda b: (b.agent_id, b.credential_id))
