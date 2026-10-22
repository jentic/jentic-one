"""Unified API-key resolver — resolves jak_ (and retired jntc_live_) keys to Identity.

Retired ``sak_`` service-account keys are refused (theme-8 Phase 4, 0.41).
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import Boolean, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.owner_status import log_agent_owner_inactive
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

AGENT_API_KEY_PREFIX = "jak_"
# Theme-8 Phase 4 (0.41) retired service accounts: every one was migrated to a
# successor agent and its tables were dropped. ``sak_`` keys no longer
# authenticate — not even as the successor — and are refused by prefix before
# any lookup, so a digest the migration copied onto a successor's credential
# row (it may be a converted ``jntc_live_`` key's, which must keep working) can
# never be reached with a ``sak_`` plaintext.
RETIRED_SERVICE_ACCOUNT_KEY_PREFIX = "sak_"
#: The 401 ``detail`` every surface answers for a ``sak_`` key.
RETIRED_SERVICE_ACCOUNT_KEY_DETAIL = (
    "Service-account keys (sak_) were retired in Jentic One 0.41: each service "
    "account was migrated to an agent. Mint a jak_ key for that agent and use it instead."
)


def is_retired_service_account_key(token: str) -> bool:
    """Whether ``token`` is a retired ``sak_`` service-account key."""
    return token.startswith(RETIRED_SERVICE_ACCOUNT_KEY_PREFIX)


# Theme-5 Phase 4 (key retirement): a retired toolkit key's plaintext keeps
# authenticating as its successor agent for the same reason (digest copied into
# ``agent_credentials``). The prefix is DEPRECATED (see
# ``docs/development/releasing.md``): each successful resolve logs a warning
# naming the actor; acceptance ends no earlier than 2026-12-01.
RETIRED_TOOLKIT_KEY_PREFIX = "jntc_live_"


class _AgentArm(enum.Enum):
    """Non-identity outcome of the agent-side digest lookup."""

    MISS = "miss"  # digest not present in agent_credentials


@dataclass(frozen=True)
class _InactiveAgent:
    """Agent-arm outcome: the digest is present, but its agent may not act.

    Carries the agent id so the fail-closed WARNING can name the successor
    the operator must re-enable. ``owner_inactive`` is set when the agent
    itself is active but its owning user is not: an owned agent follows its
    owner's account status.
    """

    agent_id: str
    owner_inactive: bool = False


class ApiKeyResolver:
    """Resolves API keys to an Identity by digest lookup in ``agent_credentials``.

    - ``jak_`` keys query ``agent_credentials`` joined to ``agents`` and the
      owner's ``users`` row: an owned agent authenticates only while both it
      and its owner are active (an ownerless agent depends on its own status
      alone).
    - ``jntc_live_`` keys (retired, deprecated) go through the same lookup:
      the theme-5 migration copied each retired key's digest onto its
      successor agent, so the key resolves as that agent. A digest miss, or a
      hit on an inactive agent, fails closed with a log line naming the next
      step.
    - ``sak_`` keys (retired in 0.41) never resolve: they are refused with an
      INFO line naming the successor agent when one holds the digest.

    Implements ``TokenResolverProtocol`` (via ``resolve_access_token``) so it
    can be wrapped by ``CachedTokenValidator``. Behind that cache (the broker),
    a status change to the agent or its owner takes effect once the cached
    verdict expires (``broker.resolve_cache_ttl_seconds``); the admin and auth
    surfaces resolve uncached, so it takes effect on the next request.
    """

    def __init__(self, admin_db: DatabaseSession) -> None:
        self._admin_db = admin_db

    async def resolve_access_token(self, token: str) -> Identity | None:
        """Protocol method — delegates to prefix-based resolve."""
        return await self.resolve(token)

    async def resolve(self, raw_key: str) -> Identity | None:
        """Hash the key and look it up in ``agent_credentials``."""
        if raw_key.startswith(AGENT_API_KEY_PREFIX):
            return await self._resolve_agent(raw_key)
        if raw_key.startswith(RETIRED_TOOLKIT_KEY_PREFIX):
            return await self._resolve_retired(
                raw_key,
                event="deprecated_toolkit_key_used",
                deadline_note="jntc_live_ acceptance ends no earlier than 2026-12-01",
            )
        if is_retired_service_account_key(raw_key):
            await self._refuse_service_account_key(raw_key)
        return None

    async def _refuse_service_account_key(self, raw_key: str) -> None:
        """Log the refusal of a retired ``sak_`` key; it never authenticates.

        INFO, not WARNING: a stale key in a client's config lands here on
        every call — an expected client-side 401, not a server fault. The
        digest lookup only names the successor agent for the operator.
        """
        row = await self._credential_row(raw_key)
        logger.info(
            "retired_service_account_key_refused",
            successor_agent_id=None if row is None else row.agent_id,
            actionable_step=(
                "Service-account (sak_) keys stopped working in 0.41. Mint a jak_ key "
                "for the successor agent and switch this caller to it (see "
                "'Upgrading to 0.41.0' in docs/development/releasing.md)."
            ),
        )

    async def _resolve_retired(
        self, raw_key: str, *, event: str, deadline_note: str
    ) -> Identity | None:
        """Retired-prefix arm: resolve as the successor agent, or fail closed.

        A successor refused only because its owner is not active returns early
        with the ``agent_owner_inactive`` event alone: the key is not logged as
        ``migrated_key_fail_closed``, which is reserved for a successor agent
        whose own status is not active.
        """
        arm = await self._lookup_agent(raw_key)
        if isinstance(arm, Identity):
            # One WARNING per resolve, naming the successor now serving the
            # key: the removal-readiness signal for the retired prefix.
            logger.warning(
                event,
                agent_id=arm.sub,
                actionable_step=(
                    "Rotate this caller to its successor agent's jak_ key; "
                    f"{deadline_note} (see docs/development/releasing.md)."
                ),
            )
            return arm
        if isinstance(arm, _InactiveAgent):
            if arm.owner_inactive:
                # Already logged as ``agent_owner_inactive`` by the lookup.
                return None
            # The successor is the authoritative identity and it is
            # disabled/archived: fail closed (the operator kill lever).
            logger.warning(
                "migrated_key_fail_closed",
                reason="successor_inactive",
                agent_id=arm.agent_id,
                actionable_step=(
                    "This key's successor agent is not active; re-enable "
                    "the agent (or mint it a fresh jak_ key) if this cut "
                    "was unintended."
                ),
            )
            return None
        # info, not warning: every stale jntc_live_ key in a client's config
        # lands here on each call; it is an expected,
        # client-caused 401, not an operator-actionable server fault.
        logger.info(
            "retired_key_unresolved",
            actionable_step=(
                "This retired key has no successor agent (it was never "
                "migrated, or its successor's key was revoked or rotated); "
                "register an agent and use its jak_ key."
            ),
        )
        return None

    async def _resolve_agent(self, raw_key: str) -> Identity | None:
        """``jak_`` arm: miss and inactive are both a plain None."""
        arm = await self._lookup_agent(raw_key)
        return arm if isinstance(arm, Identity) else None

    async def _credential_row(self, raw_key: str) -> Any:
        """The agent holding ``raw_key``'s digest, read in its own admin session."""
        async with self._admin_db.session() as session:
            return await self._fetch_credential_row(session, raw_key)

    async def _fetch_credential_row(self, session: AsyncSession, raw_key: str) -> Any:
        """The agent holding ``raw_key``'s digest.

        Columns: ``agent_id, name, status, owner_id, owner_active``.
        ``name`` rides along off the already-joined ``agents`` row (no extra
        query) so the resolved ``Identity`` can carry a human-readable actor
        name into audit event summaries (#1543). ``owner_active`` is
        ``u.active AND u.deleted_at IS NULL``, read by the same statement (an
        outer join): NULL for an ownerless agent or a missing owner row, and
        FALSE when the owner is disabled or deleted.
        """
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        stmt = text(
            "SELECT a.id AS agent_id, a.name, a.status, a.owner_id,"
            " CASE WHEN u.deleted_at IS NULL THEN u.active ELSE FALSE END AS owner_active"
            " FROM agent_credentials ac"
            " JOIN agents a ON a.id = ac.agent_id"
            " LEFT JOIN users u ON u.id = a.owner_id"
            " WHERE ac.api_key_hash = :key_hash"
        ).columns(owner_active=Boolean)
        return (await session.execute(stmt, {"key_hash": key_hash})).one_or_none()

    async def _lookup_agent(self, raw_key: str) -> Identity | _AgentArm | _InactiveAgent:
        # One admin session per lookup: the credential row and the active
        # agent's grants are read on the same pooled connection (#1703), as the
        # ``at_`` path does. Grants are only read for an agent that may act.
        async with self._admin_db.session() as session:
            row = await self._fetch_credential_row(session, raw_key)
            if row is None:
                return _AgentArm.MISS
            if row.status != "active":
                return _InactiveAgent(agent_id=row.agent_id)
            if row.owner_id is not None and not row.owner_active:
                # The owner is disabled or deleted, or its row is missing:
                # fail closed.
                log_agent_owner_inactive(row.agent_id, row.owner_id)
                return _InactiveAgent(agent_id=row.agent_id, owner_inactive=True)
            permissions = await self._load_permissions(session, row.agent_id, ActorType.AGENT)
        return Identity(
            sub=row.agent_id,
            actor_name=row.name or "",
            actor_type=ActorType.AGENT,
            permissions=permissions,
            parent_actor_id=row.owner_id,
            active=True,
        )

    async def _load_permissions(
        self, session: AsyncSession, actor_id: str, actor_type: ActorType
    ) -> list[str]:
        """The actor's granted permissions, read on the caller's ``session``."""
        stmt = text(
            "SELECT permission FROM actor_permission_grants"
            " WHERE actor_id = :actor_id AND actor_type = :actor_type"
        )
        result = await session.execute(stmt, {"actor_id": actor_id, "actor_type": actor_type.value})
        return [row.permission for row in result.all()]
