"""ServiceAccount authentication: client-credentials grant and ephemeral token minting."""

from __future__ import annotations

import hmac

from jentic_one.admin.repos import (
    ActorScopeGrantRepository,
    AgentRepository,
    ServiceAccountCredentialRepository,
    ServiceAccountRepository,
)
from jentic_one.auth.services.crypto import (
    generate_client_secret,
    generate_service_account_api_key,
    hash_secret,
)
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidGrantError,
    InvalidTransitionError,
)
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.audit import (
    AuditAction,
    AuditTargetType,
    record_audit,
    record_audit_best_effort,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, AuditReason

_MINT_TTL_MIN_SECONDS = 1
_MINT_TTL_MAX_SECONDS = 3600


class ServiceAccountAuthService:
    """Handles client-credentials authentication and ephemeral token minting for SAs."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._token_svc = TokenService(ctx)

    @property
    def access_ttl_seconds(self) -> int:
        return self._token_svc.access_ttl_seconds

    async def register_client_secret(self, service_account_id: str, *, identity: Identity) -> str:
        """Generate and store a new client secret. Returns the plaintext (shown once)."""
        secret = generate_client_secret()
        secret_hash = hash_secret(secret)

        async with self._ctx.admin_db.transaction() as session:
            sa = await ServiceAccountRepository.get_by_id_for_update(session, service_account_id)
            if sa is None:
                raise ActorNotFoundError(service_account_id)
            if sa.status != ActorStatus.ACTIVE:
                raise InvalidTransitionError(
                    service_account_id, sa.status, "generate-client-secret"
                )
            if "org:admin" not in identity.permissions and sa.owner_id != identity.sub:
                raise ActorNotFoundError(service_account_id)
            await ServiceAccountCredentialRepository.set_client_secret_hash(
                session,
                service_account_id,
                client_secret_hash=secret_hash,
                created_by=identity.sub,
            )
            await record_audit(
                session,
                action=AuditAction.ROTATE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason=AuditReason.CLIENT_SECRET_ROTATED,
                origin=identity.origin.value,
            )

        return secret

    async def register_api_key(self, service_account_id: str, *, identity: Identity) -> str:
        """Generate and store a new API key. Returns the plaintext (shown once)."""
        key = generate_service_account_api_key()
        key_hash = hash_secret(key)

        async with self._ctx.admin_db.transaction() as session:
            sa = await ServiceAccountRepository.get_by_id_for_update(session, service_account_id)
            if sa is None:
                raise ActorNotFoundError(service_account_id)
            if sa.status != ActorStatus.ACTIVE:
                raise InvalidTransitionError(service_account_id, sa.status, "generate-api-key")
            if "org:admin" not in identity.permissions and sa.owner_id != identity.sub:
                raise ActorNotFoundError(service_account_id)
            await ServiceAccountCredentialRepository.set_api_key_hash(
                session, service_account_id, api_key_hash=key_hash, created_by=identity.sub
            )
            await record_audit(
                session,
                action=AuditAction.ROTATE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason=AuditReason.API_KEY_ROTATED,
                origin=identity.origin.value,
            )

        return key

    async def authenticate_client_credentials(
        self, client_id: str, client_secret: str
    ) -> tuple[str, str, list[str]]:
        """Verify client_id + client_secret, issue an access+refresh pair.

        Returns (access_token, refresh_token, scopes). ``scopes`` is the
        service account's live ``actor_scope_grants`` set stamped on the
        minted pair, returned so the token endpoint can report the effective
        scope per RFC 6749 §5.1.
        Raises InvalidGrantError on authentication failure.
        """
        async with self._ctx.admin_db.session() as session:
            sa = await ServiceAccountRepository.get_by_id(session, client_id)
            if sa is None or sa.status != ActorStatus.ACTIVE:
                raise InvalidGrantError("invalid_client")

            cred = await ServiceAccountCredentialRepository.get_by_service_account_id(
                session, client_id
            )
            if cred is None or cred.client_secret_hash is None:
                raise InvalidGrantError("invalid_client")

            if not hmac.compare_digest(hash_secret(client_secret), cred.client_secret_hash):
                raise InvalidGrantError("invalid_client")

            grants = await ActorScopeGrantRepository.list_for_actor(
                session, client_id, actor_type=ActorType.SERVICE_ACCOUNT
            )
            scopes = [g.scope for g in grants]

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.LOGIN,
            target_type=AuditTargetType.SESSION,
            target_id=client_id,
            actor_type=ActorType.SERVICE_ACCOUNT,
            actor_id=client_id,
            reason="client credentials grant",
            origin=None,
        )

        access_token, refresh_token = await self._token_svc.issue_pair(
            client_id, ActorType.SERVICE_ACCOUNT, scopes
        )
        return access_token, refresh_token, scopes

    async def mint_task_token(
        self,
        *,
        host_sa_id: str,
        host_sa_scopes: list[str],
        requested_scopes: list[str],
        target_agent_id: str,
        ttl_seconds: int | None = None,
    ) -> str:
        """Mint a short-lived ephemeral token for a task/agent.

        Security-critical: requested_scopes MUST be a subset of host_sa_scopes.
        The target agent must exist and be ACTIVE — resolvers re-check actor
        status on every verdict (#1136), so minting for a non-active agent
        would return a token that is dead on arrival; fail fast instead.
        Returns an opaque access token (no refresh token for ephemeral mints).
        """
        host_scope_set = set(host_sa_scopes)
        requested_set = set(requested_scopes)
        excess = requested_set - host_scope_set
        if excess:
            raise InvalidGrantError("invalid_scope: requested scopes exceed host SA grants")

        if not requested_scopes:
            raise InvalidGrantError("invalid_scope: at least one scope is required")

        ttl = ttl_seconds if ttl_seconds is not None else 300
        if ttl < _MINT_TTL_MIN_SECONDS or ttl > _MINT_TTL_MAX_SECONDS:
            raise InvalidGrantError(
                f"invalid_request: ttl_seconds must be between"
                f" {_MINT_TTL_MIN_SECONDS} and {_MINT_TTL_MAX_SECONDS}"
            )

        # One message for missing and non-active: no oracle for probing agent ids.
        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, target_agent_id)
        if agent is None or agent.status != ActorStatus.ACTIVE:
            raise InvalidGrantError(
                "invalid_request: target_agent_id does not resolve to an active agent"
            )

        return await self._token_svc.issue_access_only(
            target_agent_id, ActorType.AGENT, requested_scopes, ttl_seconds=ttl
        )
