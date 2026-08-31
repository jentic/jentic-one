"""Agent authentication: API key, client secret generation, and client_credentials grant."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos import (
    AgentCredentialRepository,
    AgentRepository,
    AuditRepository,
)
from jentic_one.auth.services.crypto import (
    generate_agent_api_key,
    hash_secret,
)
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidTransitionError,
    NoApiKeyError,
)
from jentic_one.auth.services.schemas.api_key_info import ApiKeyHistoryEntry, ApiKeyInfo
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.audit import (
    AuditAction,
    AuditTargetType,
    record_audit,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, AuditReason


class AgentAuthService:
    """Handles credential generation and client_credentials auth for agents."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._token_svc = TokenService(ctx)

    @property
    def access_ttl_seconds(self) -> int:
        return self._token_svc.access_ttl_seconds

    async def register_api_key(self, agent_id: str, *, identity: Identity) -> str:
        """Generate and store a new API key. Returns the plaintext (shown once)."""
        key = generate_agent_api_key()
        key_hash = hash_secret(key)

        async with self._ctx.admin_db.transaction() as session:
            await self._ensure_active_and_owned(session, agent_id, identity)
            await AgentCredentialRepository.set_api_key_hash(
                session, agent_id, api_key_hash=key_hash, created_by=identity.sub
            )
            await record_audit(
                session,
                action=AuditAction.ROTATE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason=AuditReason.API_KEY_ROTATED,
                origin=identity.origin.value,
            )

        return key

    async def revoke_api_key(self, agent_id: str, *, identity: Identity) -> None:
        """Revoke (nullify) the agent's API key without generating a new one."""
        async with self._ctx.admin_db.transaction() as session:
            await self._ensure_active_and_owned(session, agent_id, identity)
            revoked = await AgentCredentialRepository.clear_api_key_hash(session, agent_id)
            if not revoked:
                raise NoApiKeyError(agent_id)
            await record_audit(
                session,
                action=AuditAction.REVOKE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason=AuditReason.API_KEY_REVOKED,
                origin=identity.origin.value,
            )

    async def get_api_key_info(self, agent_id: str, *, identity: Identity) -> ApiKeyInfo | None:
        """Return API key metadata for an agent, or None if no key was ever generated."""
        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)
            if agent is None:
                raise ActorNotFoundError(agent_id)
            if "org:admin" not in identity.permissions and agent.owner_id != identity.sub:
                raise ActorNotFoundError(agent_id)
            cred = await AgentCredentialRepository.get_by_agent_id(session, agent_id)
            if cred is None:
                return None
            return ApiKeyInfo(
                id=cred.id,
                status="active" if cred.api_key_hash is not None else "revoked",
                created_at=cred.created_at,
                rotated_at=cred.rotated_at,
                created_by=cred.created_by,
            )

    async def get_api_key_history(
        self, agent_id: str, *, identity: Identity
    ) -> list[ApiKeyHistoryEntry]:
        """Return the audit trail of API key operations for an agent."""
        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)
            if agent is None:
                raise ActorNotFoundError(agent_id)
            if "org:admin" not in identity.permissions and agent.owner_id != identity.sub:
                raise ActorNotFoundError(agent_id)
            entries = await AuditRepository.list_by_target(
                session,
                AuditTargetType.AGENT,
                agent_id,
                limit=50,
            )
            api_key_reasons = {AuditReason.API_KEY_ROTATED, AuditReason.API_KEY_REVOKED}
            return [
                ApiKeyHistoryEntry(
                    id=e.id,
                    action=e.action,
                    reason=e.reason,
                    actor_id=e.actor_id,
                    occurred_at=e.occurred_at,
                )
                for e in entries
                if e.reason in api_key_reasons
            ]

    @staticmethod
    async def _ensure_active_and_owned(
        session: AsyncSession, agent_id: str, identity: Identity
    ) -> None:
        """Validate active status and ownership inside the transaction (SELECT FOR UPDATE)."""
        agent = await AgentRepository.get_by_id_for_update(session, agent_id)
        if agent is None:
            raise ActorNotFoundError(agent_id)
        if agent.status != ActorStatus.ACTIVE:
            raise InvalidTransitionError(agent_id, agent.status, "generate-api-key")
        if "org:admin" not in identity.permissions and agent.owner_id != identity.sub:
            raise ActorNotFoundError(agent_id)
