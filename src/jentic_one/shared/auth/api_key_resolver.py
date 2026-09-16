"""Unified API-key resolver — resolves jak_/sak_ (and retired jntc_live_) keys to Identity."""

from __future__ import annotations

import enum
import hashlib
from typing import Any

import structlog
from sqlalchemy import text

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.telemetry.events import TelemetryEventName
from jentic_one.shared.telemetry.sink import TelemetrySink, get_active_sink

logger = structlog.get_logger(__name__)

AGENT_API_KEY_PREFIX = "jak_"
SERVICE_ACCOUNT_API_KEY_PREFIX = "sak_"
# Theme-5 Phase 4 (key retirement): a retired toolkit key's plaintext keeps
# authenticating — as the actor the retirement job created for it — because
# the job copies the key's SHA-256 lookup digest into the credential table and
# this resolver matches by digest, not by prefix. The prefix is DEPRECATED
# (see the deprecation notice in ``docs/releasing.md``): each successful
# resolve logs a warning naming the actor, and the acceptance is deleted with
# the toolkit surface.
RETIRED_TOOLKIT_KEY_PREFIX = "jntc_live_"


class _AgentArm(enum.Enum):
    """Non-identity outcomes of the agent-side digest lookup.

    The distinction matters on the ``sak_``/``jntc_live_`` arm (H1): a digest
    that EXISTS in ``agent_credentials`` but belongs to an inactive agent must
    FAIL CLOSED — the successor agent is the operator's kill lever during the
    coexistence window, and falling through to the still-active SA row would
    resurrect the key the operator just cut.
    """

    MISS = "miss"  # digest not present in agent_credentials
    INACTIVE = "inactive"  # digest present, but the agent is not active


class ApiKeyResolver:
    """Resolves API keys to an Identity by prefix-dispatched DB lookup.

    - ``jak_`` keys query ``agent_credentials`` joined to ``agents``.
    - ``sak_`` and ``jntc_live_`` (deprecated) keys are **agent-first**
      (theme-8 Phase 1, H-2): the migration job copies each service account's
      key digest into ``agent_credentials``, so a migrated key resolves as
      its successor agent. The SA fallback is consulted only on a genuine
      digest MISS on the agent arm; a digest-hit on an inactive agent fails
      closed, and a stamped SA row (``migrated_to_actor_id`` non-null) is
      never a valid identity source — so disabling the successor or revoking
      its key both cut the old plaintext (H1, the operator kill levers).
      Keys not yet migrated miss the agent lookup and
      fall back to ``service_account_credentials`` joined to
      ``service_accounts`` — each fallback hit logs a WARNING and bumps the
      ``service_account_fallback_resolve`` telemetry counter (trending to
      zero is the sweep-readiness signal). Never a hard repoint: the boot
      migration job is fire-and-forget and old-image pods must keep serving
      migrated keys through a rolling upgrade.

    Implements ``TokenResolverProtocol`` (via ``resolve_access_token``) so it
    can be wrapped by ``CachedTokenValidator``.
    """

    def __init__(self, admin_db: DatabaseSession, telemetry: TelemetrySink | None = None) -> None:
        self._admin_db = admin_db
        # IMPL-DECISION 4 deviation (documented): construction sites run
        # before the lifespan wires ctx.telemetry, so the injected handle is
        # always None in practice; _record_fallback falls back to the
        # process-global active sink at record time. The parameter stays so
        # tests can inject a sink directly.
        self._telemetry = telemetry

    async def resolve_access_token(self, token: str) -> Identity | None:
        """Protocol method — delegates to prefix-based resolve."""
        return await self.resolve(token)

    async def resolve(self, raw_key: str) -> Identity | None:
        """Hash the key and look it up in the appropriate credential table."""
        if raw_key.startswith(AGENT_API_KEY_PREFIX):
            return await self._resolve_agent(raw_key)
        if raw_key.startswith(SERVICE_ACCOUNT_API_KEY_PREFIX) or raw_key.startswith(
            RETIRED_TOOLKIT_KEY_PREFIX
        ):
            # Agent-first (H-2): a migrated key's digest lives in
            # agent_credentials, so it resolves as its successor agent.
            arm = await self._lookup_agent(raw_key)
            if isinstance(arm, Identity):
                if raw_key.startswith(RETIRED_TOOLKIT_KEY_PREFIX):
                    # M3: the theme-5 6b removal-readiness signal must not go
                    # dark after migration — WARN on the agent arm too, one
                    # per resolve, naming the successor now serving the key.
                    logger.warning(
                        "deprecated_toolkit_key_used",
                        agent_id=arm.sub,
                        actionable_step=(
                            "Rotate this caller to its successor agent's jak_ "
                            "key; jntc_live_ acceptance is removed with the "
                            "toolkit surface."
                        ),
                    )
                return arm
            if arm is _AgentArm.INACTIVE:
                # H1: the digest EXISTS on the agent side — the successor is
                # the authoritative identity and it is disabled/archived.
                # FAIL CLOSED; never consult the SA fallback (it would
                # resurrect the key the operator just cut).
                logger.warning(
                    "migrated_key_fail_closed",
                    reason="successor_inactive",
                    actionable_step=(
                        "This key's successor agent is not active; re-enable "
                        "the agent (or mint it a fresh jak_ key) if this cut "
                        "was unintended."
                    ),
                )
                return None
            # Genuine digest MISS on the agent arm: consult the SA fallback.
            sa_row = await self._lookup_service_account_row(raw_key)
            if sa_row is not None and sa_row.migrated_to_actor_id is not None:
                # H1: a stamped SA is never a valid identity source — the
                # successor's digest was NULLed (key revoked/rotated) or the
                # row was skip-but-stamped. FAIL CLOSED regardless of SA
                # status: the SA-side mutation guards 409 the direct kill
                # levers, so this arm must not keep the key alive.
                logger.warning(
                    "migrated_key_fail_closed",
                    reason="stamped_service_account",
                    service_account_id=sa_row.service_account_id,
                    actionable_step=(
                        "This key's account is migrated and its successor "
                        "agent no longer carries the digest; mint the "
                        "successor a fresh jak_ key if access should resume."
                    ),
                )
                return None
            identity = await self._identity_from_sa_row(sa_row)
            if identity is not None:
                # WARNING + counter (L-C): pod-local stdout is not alertable.
                logger.warning(
                    "service_account_fallback_resolve",
                    service_account_id=identity.sub,
                    actionable_step=(
                        "This key's account is not yet migrated; run "
                        "`jentic_one migrate-service-accounts` (or wait for the "
                        "boot job) so it resolves as its successor agent."
                    ),
                )
                self._record_fallback()
                if raw_key.startswith(RETIRED_TOOLKIT_KEY_PREFIX):
                    # WARNING (not info): this stays the theme-5 6b migration
                    # signal — each line names a caller still presenting a
                    # retired key form.
                    logger.warning(
                        "deprecated_toolkit_key_used",
                        service_account_id=identity.sub,
                        actionable_step=(
                            "Rotate this caller to its successor agent's jak_ key "
                            "(run `jentic_one migrate-service-accounts` if not yet "
                            "migrated); jntc_live_ acceptance is removed with the "
                            "toolkit surface."
                        ),
                    )
            return identity
        return None

    def _record_fallback(self) -> None:
        """Bump the fallback counter on the injected or process-global sink."""
        sink = self._telemetry or get_active_sink()
        if sink is not None:
            sink.record(
                TelemetryEventName.SERVICE_ACCOUNT_FALLBACK_RESOLVE,
                actor_type=ActorType.SERVICE_ACCOUNT.value,
            )

    async def _resolve_agent(self, raw_key: str) -> Identity | None:
        """``jak_`` arm: miss and inactive are both a plain None (no fallback)."""
        arm = await self._lookup_agent(raw_key)
        return arm if isinstance(arm, Identity) else None

    async def _lookup_agent(self, raw_key: str) -> Identity | _AgentArm:
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
            return _AgentArm.INACTIVE

        permissions = await self._load_permissions(row.agent_id, ActorType.AGENT)
        return Identity(
            sub=row.agent_id,
            actor_type=ActorType.AGENT,
            permissions=permissions,
            parent_actor_id=row.owner_id,
            active=True,
        )

    async def _resolve_service_account(self, raw_key: str) -> Identity | None:
        """The pre-theme-8 SA arm, stamp-blind — kept as the old-image-pod
        simulation for rolling-upgrade tests (H-B); ``resolve`` itself applies
        the H1 stamp check on the fallback."""
        row = await self._lookup_service_account_row(raw_key)
        return await self._identity_from_sa_row(row)

    async def _lookup_service_account_row(self, raw_key: str) -> Any:
        """The SA credential/row lookup (with the theme-8 stamp), or None."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        stmt = text(
            "SELECT sa.id AS service_account_id, sa.status, sa.migrated_to_actor_id"
            " FROM service_account_credentials sac"
            " JOIN service_accounts sa ON sa.id = sac.service_account_id"
            " WHERE sac.api_key_hash = :key_hash"
        )
        async with self._admin_db.session() as session:
            return (await session.execute(stmt, {"key_hash": key_hash})).one_or_none()

    async def _identity_from_sa_row(self, row: Any) -> Identity | None:
        """Old SA-arm identity rules (active-only), stamp-blind."""
        if row is None:
            return None
        if row.status != "active":
            return None

        permissions = await self._load_permissions(
            row.service_account_id, ActorType.SERVICE_ACCOUNT
        )
        return Identity(
            sub=row.service_account_id,
            actor_type=ActorType.SERVICE_ACCOUNT,
            permissions=permissions,
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
