"""The denied agent's open connect session for an API, read from the control DB.

When the broker denies an execute because no credential is bound or
provisioned (403 ``no_credential_binding`` / 424 ``credential_not_provisioned``),
the agent may already have asked for one: an agent-initiated connect session
still awaiting its owner. The denial directive then links the owner to that
session (``provisioning_url``) instead of having the agent open another.

A session targets an API through its upfront ``pending`` credential, whose
stored identity carries the vendor's API axes, so coverage is decided by the
same shared fragment the binding resolver uses
(``shared.models.api_identity.credential_coverage_where``). Raw SQL because
the broker may not import the ``control`` ORM.
"""

from __future__ import annotations

from sqlalchemy import text

from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models.api_identity import credential_coverage_where

# Live states mirror ``control.repos.connect_session_repo.LIVE_STATES``:
# ``created`` waits for the owner's review, ``polling`` for the vendor flow.
# Newest first, so the link names the session the agent opened last.
_OPEN_SESSION_FOR_API = text(
    "SELECT cs.id FROM connect_sessions cs "
    "JOIN credentials c ON c.id = cs.credential_id "
    "WHERE cs.agent_id = :agent_id AND cs.state IN ('created', 'polling') "
    f"AND {credential_coverage_where()} "
    "ORDER BY cs.created_at DESC, cs.id DESC LIMIT 1"
)


class OpenConnectSessionReader:
    """Finds an agent's newest open connect session whose credential covers an API."""

    def __init__(self, control_db: DatabaseSession) -> None:
        self._control_db = control_db

    async def find_session_id(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> str | None:
        """Return the open session's id, or ``None`` when the agent has none for the API."""
        async with self._control_db.session() as session:
            row = (
                await session.execute(
                    _OPEN_SESSION_FOR_API,
                    {"agent_id": agent_id, "vendor": vendor, "name": name, "version": version},
                )
            ).first()
        return str(row[0]) if row is not None else None
