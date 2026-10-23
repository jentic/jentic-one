"""Cross-database check: does an API have credentials bound to a consumer?

Backs the server-host change guard: a catalog re-import or a promote that
changes an API's server hosts is held for operator review only when the API
has stored credentials that some agent is bound to (direct agent bindings
are the only binding form since theme-5 Phase 6b dropped the toolkit tables),
or an open connect session that targets it, because only then does the host
change redirect where a stored (or about-to-be-entered) secret is sent.

Registry, control and admin are **separate databases**, and the registry
module may import neither ``admin`` nor ``control`` ORM, so both legs run as
raw SQL against sessions handed in by the caller (the same boundary pattern as
``governed_hosts_repo.py``). Deliberately NOT filtered on ``credentials.active``,
``credentials.state`` or ``agent_credential_bindings.suspended``: a credential
or binding that is off today can be switched back on without touching the
registry, so it still counts.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.models.api_identity import credential_coverage_where

# control DB — every credential covering the API identity (NULL axis = wildcard,
# same fragment the broker resolver uses).
_COVERING_CREDENTIALS = text(
    f"SELECT c.id FROM credentials c WHERE {credential_coverage_where()} ORDER BY c.id"
)

# control DB — an open connect session targeting the API identity. Its pending
# credential is not bound yet, but a human may be about to enter a secret that
# goes to the hosts pinned at ``:connect``.
_OPEN_API_TARGET_SESSION = text(
    "SELECT 1 FROM connect_sessions WHERE target_kind = 'api' "
    "AND state IN ('created', 'awaiting_app', 'polling') "
    "AND vendor = :vendor AND api_name = :name AND api_version = :version LIMIT 1"
)

# admin DB — direct agent bindings to those credentials.
_AGENT_BOUND = text(
    "SELECT 1 FROM agent_credential_bindings WHERE credential_id IN :credential_ids LIMIT 1"
).bindparams(bindparam("credential_ids", expanding=True))


class CredentialBindingPresenceRepository:
    """Each method runs against the session for one database; the caller sequences them."""

    @staticmethod
    async def covering_credential_ids(
        session: AsyncSession, *, vendor: str, name: str, version: str
    ) -> list[str]:
        """Ids of every credential covering the API identity (**control** DB session)."""
        rows = (
            await session.execute(
                _COVERING_CREDENTIALS, {"vendor": vendor, "name": name, "version": version}
            )
        ).all()
        return [row[0] for row in rows]

    @staticmethod
    async def any_open_api_target_session(
        session: AsyncSession, *, vendor: str, name: str, version: str
    ) -> bool:
        """True when an open connect session targets the API identity (**control** DB)."""
        row = (
            await session.execute(
                _OPEN_API_TARGET_SESSION, {"vendor": vendor, "name": name, "version": version}
            )
        ).first()
        return row is not None

    @staticmethod
    async def any_agent_binding(session: AsyncSession, *, credential_ids: list[str]) -> bool:
        """True when an agent binds any of the credentials (**admin** DB session)."""
        if not credential_ids:
            return False
        row = (await session.execute(_AGENT_BOUND, {"credential_ids": credential_ids})).first()
        return row is not None
