"""Unified API-key resolver — resolves jak_/sak_ (and retired jntc_live_) keys to Identity."""

from __future__ import annotations

import hashlib

import structlog
from sqlalchemy import text

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

AGENT_API_KEY_PREFIX = "jak_"
SERVICE_ACCOUNT_API_KEY_PREFIX = "sak_"
# Theme-5 Phase 4 (key retirement): a retired toolkit key's plaintext keeps
# authenticating — as the service account the retirement job created for it —
# because the job copies the key's SHA-256 lookup digest into
# ``service_account_credentials.api_key_hash`` and this resolver matches by
# digest, not by prefix. The prefix is DEPRECATED (see the deprecation notice
# in ``docs/releasing.md``): each successful resolve logs a warning naming
# the account, and the acceptance is deleted with the toolkit surface.
RETIRED_TOOLKIT_KEY_PREFIX = "jntc_live_"


class ApiKeyResolver:
    """Resolves API keys to an Identity by prefix-dispatched DB lookup.

    - ``jak_`` keys query ``agent_credentials`` joined to ``agents``.
    - ``sak_`` keys query ``service_account_credentials`` joined to ``service_accounts``.
    - ``jntc_live_`` keys (deprecated) resolve through the service-account
      lookup — the retirement job migrates each key's digest there.

    Implements ``TokenResolverProtocol`` (via ``resolve_access_token``) so it
    can be wrapped by ``CachedTokenValidator``.
    """

    def __init__(self, admin_db: DatabaseSession) -> None:
        self._admin_db = admin_db

    async def resolve_access_token(self, token: str) -> Identity | None:
        """Protocol method — delegates to prefix-based resolve."""
        return await self.resolve(token)

    async def resolve(self, raw_key: str) -> Identity | None:
        """Hash the key and look it up in the appropriate credential table."""
        if raw_key.startswith(AGENT_API_KEY_PREFIX):
            return await self._resolve_agent(raw_key)
        if raw_key.startswith(SERVICE_ACCOUNT_API_KEY_PREFIX):
            return await self._resolve_service_account(raw_key)
        if raw_key.startswith(RETIRED_TOOLKIT_KEY_PREFIX):
            identity = await self._resolve_service_account(raw_key)
            if identity is not None:
                # WARNING (not info): this is the operator's migration signal —
                # each line names a caller still presenting a retired key form.
                logger.warning(
                    "deprecated_toolkit_key_used",
                    service_account_id=identity.sub,
                    actionable_step=(
                        "Rotate this caller to its service account's sak_ key; "
                        "jntc_live_ acceptance is removed with the toolkit surface."
                    ),
                )
            return identity
        return None

    async def _resolve_agent(self, raw_key: str) -> Identity | None:
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
            return None
        if row.status != "active":
            return None

        permissions = await self._load_permissions(row.agent_id, ActorType.AGENT)
        return Identity(
            sub=row.agent_id,
            actor_type=ActorType.AGENT,
            permissions=permissions,
            parent_actor_id=row.owner_id,
            active=True,
        )

    async def _resolve_service_account(self, raw_key: str) -> Identity | None:
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        stmt = text(
            "SELECT sa.id AS service_account_id, sa.status"
            " FROM service_account_credentials sac"
            " JOIN service_accounts sa ON sa.id = sac.service_account_id"
            " WHERE sac.api_key_hash = :key_hash"
        )
        async with self._admin_db.session() as session:
            row = (await session.execute(stmt, {"key_hash": key_hash})).one_or_none()

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
