"""Cross-DB toolkit-derivation resolver (admin bindings ∩ control credential bindings).

Given an agent and a resolved API identity, returns the agent's toolkit IDs that
contain that API. This is inherently cross-schema (admin agent→toolkit bindings +
control toolkit→credential bindings), and the broker may import neither ``admin``
nor ``control`` ORM — so it runs as raw SQL behind ``ToolkitDeriverProtocol``.

The two databases are separate sessions, so the intersection is computed in
Python rather than via a cross-schema JOIN.
"""

from __future__ import annotations

from sqlalchemy import text

from jentic_one.shared.db import DatabaseSession

# admin DB — the toolkits the agent is bound to.
_AGENT_TOOLKITS = text("SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :agent_id")

# control DB — toolkits whose bound credential matches the API identity. Empty
# string for :name / :version means "any" (the credential may not pin name/version).
# NULL api_name/api_version on the credential means "covers all names/versions for this vendor".
#
# NB: this run-time resolver intentionally treats a NULL-wildcard credential
# binding as a valid match and applies *no* exact-name preference — at execution
# time we want every toolkit the agent can legitimately use, including
# wildcard-credential ones. This differs from the *bind-time* resolver
# (control/repos/effects_repo.resolve_toolkits_for_api), which prefers an exact
# api_name match so the approver binds the most specific toolkit. The two serve
# different purposes (credential coverage vs ownership-scoped binding selection),
# so the asymmetry is deliberate: a bind picks the narrowest toolkit, while an
# execute accepts any covering one (surfacing ambiguous_toolkit when >1 match).
_TOOLKITS_FOR_API = text(
    "SELECT DISTINCT tcb.toolkit_id "
    "FROM toolkit_credential_bindings tcb "
    "JOIN credentials c ON c.id = tcb.credential_id "
    "WHERE c.api_vendor = :vendor "
    "  AND (:name = '' OR c.api_name IS NULL OR c.api_name = :name) "
    "  AND (:version = '' OR c.api_version IS NULL OR c.api_version = :version)"
)


class ToolkitBindingResolver:
    """Derives an agent's toolkits for an API identity across the admin + control DBs."""

    def __init__(self, admin_db: DatabaseSession, control_db: DatabaseSession) -> None:
        self._admin_db = admin_db
        self._control_db = control_db

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> list[str]:
        """Return the sorted toolkit IDs the agent has that contain the given API."""
        async with self._admin_db.session() as session:
            agent_rows = (await session.execute(_AGENT_TOOLKITS, {"agent_id": agent_id})).all()
        agent_toolkits = {row[0] for row in agent_rows}
        if not agent_toolkits:
            return []

        async with self._control_db.session() as session:
            api_rows = (
                await session.execute(
                    _TOOLKITS_FOR_API,
                    {"vendor": vendor, "name": name, "version": version},
                )
            ).all()
        api_toolkits = {row[0] for row in api_rows}

        return sorted(agent_toolkits & api_toolkits)

    async def any_toolkit_serves_api(self, *, vendor: str, name: str, version: str) -> bool:
        """Return whether any toolkit serves the given API (independent of the agent).

        Runs the same control-DB toolkit↔API predicate as :meth:`derive_toolkits`
        but without intersecting the agent's bindings, so a ``no_toolkit_binding``
        denial can tell apart "no toolkit exists yet" (provision a credential
        first) from "a toolkit serves it but this agent isn't bound" (file a
        toolkit binding). See issue #683.
        """
        async with self._control_db.session() as session:
            api_rows = (
                await session.execute(
                    _TOOLKITS_FOR_API,
                    {"vendor": vendor, "name": name, "version": version},
                )
            ).all()
        return bool(api_rows)
