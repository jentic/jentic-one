"""Dynamic Client Registration service (RFC 7591 subset)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import ActorScopeGrantRepository
from jentic_one.admin.repos.agent_repo import AgentRepository
from jentic_one.auth.services.errors import InvalidGrantError, RegistrationAccessDeniedError
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType

RAT_PREFIX = "rat_"


@dataclass(frozen=True, slots=True)
class RegisterResult:
    """Result of a successful dynamic registration."""

    client_id: str
    registration_access_token: str
    registration_client_uri: str
    status: str


@dataclass(frozen=True, slots=True)
class PollResult:
    """Result of polling registration status."""

    client_id: str
    status: str


def _hash_rat(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_rat() -> str:
    return f"{RAT_PREFIX}{secrets.token_urlsafe(32)}"


_MAX_JWKS_KEYS = 5

_MAX_JWKS_BYTES = 8192


def _validate_jwks(jwks: dict[str, Any]) -> None:
    """Validate that JWKS contains at least one Ed25519 public key and no private material."""
    if len(json.dumps(jwks)) > _MAX_JWKS_BYTES:
        raise InvalidGrantError(f"jwks exceeds maximum size of {_MAX_JWKS_BYTES} bytes")

    keys = jwks.get("keys")
    if not isinstance(keys, list) or len(keys) == 0:
        raise InvalidGrantError("jwks must contain at least one key in 'keys' array")

    if len(keys) > _MAX_JWKS_KEYS:
        raise InvalidGrantError(f"jwks must contain at most {_MAX_JWKS_KEYS} keys")

    has_ed25519 = False
    for key in keys:
        if not isinstance(key, dict):
            raise InvalidGrantError("Each key in jwks must be a JSON object")
        if key.get("d"):
            raise InvalidGrantError("jwks must not contain private key material")
        if key.get("kty") == "OKP" and key.get("crv") == "Ed25519":
            if not key.get("x"):
                raise InvalidGrantError("Ed25519 key must include 'x' parameter")
            has_ed25519 = True

    if not has_ed25519:
        raise InvalidGrantError(
            "jwks must contain at least one Ed25519 public key (kty=OKP, crv=Ed25519)"
        )


class RegistrationService:
    """Handles agent self-registration via Dynamic Client Registration."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def register(
        self, client_name: str, jwks: dict[str, Any], *, scope: str | None = None
    ) -> RegisterResult:
        """Register a new agent with its public key set."""
        _validate_jwks(jwks)

        rat_plain = _generate_rat()
        rat_hash = _hash_rat(rat_plain)
        rat_ttl = self._ctx.config.auth.rat_ttl_seconds
        rat_expires_at = datetime.now(UTC) + timedelta(seconds=rat_ttl)

        async def _write(session: AsyncSession) -> Agent:
            agent = await AgentRepository.create_dcr(
                session,
                name=client_name,
                jwks=jwks,
                rat_hash=rat_hash,
                rat_expires_at=rat_expires_at,
            )
            if scope:
                for scope_value in list(dict.fromkeys(scope.split())):
                    await ActorScopeGrantRepository.grant(
                        session,
                        actor_id=agent.id,
                        actor_type=ActorType.AGENT,
                        scope=scope_value,
                        granted_by=None,
                        created_by="dcr",
                    )
            await record_audit(
                session,
                action=AuditAction.REGISTER,
                target_type=AuditTargetType.AGENT,
                target_id=agent.id,
                actor_type=ActorType.AGENT,
                actor_id=agent.id,
                after={"name": client_name, "status": agent.status},
                reason="dynamic client registration",
                origin=None,
            )
            await emit_event_best_effort(
                session,
                type=EventType.AGENT_SELF_REGISTERED,
                severity=EventSeverity.INFO,
                summary=f"Agent {agent.id} self-registered",
                created_by="dcr",
                actor_id=agent.id,
                actor_type=ActorType.AGENT.value,
            )
            return agent

        # Route through run_in_transaction so a transient SQLite write-lock —
        # DCR contends on the admin DB with the background job worker's poll and
        # concurrent token-mint traffic — is retried (with WAL + busy_timeout)
        # rather than surfaced as a 500 "database is locked" on first contention.
        # The RAT is generated above so its plaintext stays stable across a retry.
        agent = await self._ctx.admin_db.run_in_transaction(_write)

        base_url = self._ctx.config.auth.canonical_base_url
        return RegisterResult(
            client_id=agent.id,
            registration_access_token=rat_plain,
            registration_client_uri=f"{base_url}/register/{agent.id}",
            status=agent.status,
        )

    async def poll_status(self, agent_id: str, rat: str) -> PollResult:
        """Check agent registration status using the registration access token."""
        rat_hash = _hash_rat(rat)

        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)

        if agent is None:
            raise RegistrationAccessDeniedError("invalid registration access token")

        if not hmac.compare_digest(agent.registration_access_token_hash or "", rat_hash):
            raise RegistrationAccessDeniedError("invalid registration access token")

        if agent.rat_expires_at is not None and agent.rat_expires_at < datetime.now(UTC):
            raise RegistrationAccessDeniedError("registration access token expired")

        return PollResult(client_id=agent.id, status=agent.status)
