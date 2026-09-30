"""Unified API-key resolver — resolves jak_ (and retired sak_/jntc_live_) keys to Identity."""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass

import structlog
from sqlalchemy import text

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

AGENT_API_KEY_PREFIX = "jak_"
# Theme-8 Phase 4 dropped the service-account tables. A ``sak_`` key keeps
# authenticating as its successor agent: the Phase-1 migration copied its
# SHA-256 lookup digest into ``agent_credentials`` and this resolver matches by
# digest, not by prefix. The prefix is DEPRECATED (see
# ``docs/development/releasing.md``); each successful resolve logs a warning
# naming the successor agent.
RETIRED_SERVICE_ACCOUNT_KEY_PREFIX = "sak_"
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
    """Agent-arm outcome: the digest is present, but its agent is not active.

    Carries the agent id so the fail-closed WARNING can name the successor
    the operator must re-enable.
    """

    agent_id: str


class ApiKeyResolver:
    """Resolves API keys to an Identity by digest lookup in ``agent_credentials``.

    - ``jak_`` keys query ``agent_credentials`` joined to ``agents``.
    - ``sak_`` and ``jntc_live_`` keys (both retired, both deprecated) go
      through the same lookup: the theme-8 / theme-5 migrations copied each
      retired key's digest onto its successor agent, so the key resolves as
      that agent. There is no fallback (theme-8 Phase 4 dropped the
      service-account tables): a digest miss, or a hit on an inactive agent,
      fails closed with a WARNING naming the next step.

    Implements ``TokenResolverProtocol`` (via ``resolve_access_token``) so it
    can be wrapped by ``CachedTokenValidator``.
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
        if raw_key.startswith(RETIRED_SERVICE_ACCOUNT_KEY_PREFIX):
            return await self._resolve_retired(
                raw_key,
                event="deprecated_service_account_key_used",
                deadline_note="sak_ acceptance is deprecated",
            )
        return None

    async def _resolve_retired(
        self, raw_key: str, *, event: str, deadline_note: str
    ) -> Identity | None:
        """Retired-prefix arm: resolve as the successor agent, or fail closed."""
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
        # info, not warning: after Phase 4 every stale sak_/jntc_live_ key in
        # a client's config lands here on each call; it is an expected,
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

    async def _lookup_agent(self, raw_key: str) -> Identity | _AgentArm | _InactiveAgent:
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        stmt = text(
            "SELECT a.id AS agent_id, a.status, a.owner_id"
            " FROM agent_credentials ac"
            " JOIN agents a ON a.id = ac.agent_id"
            " WHERE ac.api_key_hash = :key_hash"
        )
        async with self._admin_db.session() as session:
            row = (await session.execute(stmt, {"key_hash": key_hash})).one_or_none()

        if row is None:
            return _AgentArm.MISS
        if row.status != "active":
            return _InactiveAgent(agent_id=row.agent_id)

        permissions = await self._load_permissions(row.agent_id, ActorType.AGENT)
        return Identity(
            sub=row.agent_id,
            actor_type=ActorType.AGENT,
            permissions=permissions,
            parent_actor_id=row.owner_id,
            active=True,
        )

    async def _load_permissions(self, actor_id: str, actor_type: ActorType) -> list[str]:
        stmt = text(
            "SELECT scope FROM actor_scope_grants"
            " WHERE actor_id = :actor_id AND actor_type = :actor_type"
        )
        async with self._admin_db.session() as session:
            result = await session.execute(
                stmt, {"actor_id": actor_id, "actor_type": actor_type.value}
            )
            return [row.scope for row in result.all()]
