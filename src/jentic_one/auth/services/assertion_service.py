"""JWT Bearer assertion verification and token exchange service (RFC 7523)."""

from __future__ import annotations

import time
from typing import Any

import jwt

from jentic_one.admin.repos.actor_scope_grant_repo import ActorScopeGrantRepository
from jentic_one.admin.repos.agent_repo import AgentRepository
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth import resolve_agent_key
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType

_INVALID = "Assertion is invalid"

_DEFAULT_MAX_TTL = 300


class _JTICache:
    """In-process JTI replay cache with lazy TTL eviction."""

    def __init__(self, max_ttl: int) -> None:
        self._max_ttl = max_ttl
        self._seen: dict[str, float] = {}

    def check_and_insert(self, jti: str) -> bool:
        """Return True if jti is new (not replayed). Inserts it for future checks."""
        now = time.time()
        self._evict(now)
        if jti in self._seen:
            return False
        self._seen[jti] = now + self._max_ttl
        return True

    def _evict(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp <= now]
        for k in expired:
            del self._seen[k]


# Module-level singleton — survives across requests for actual replay protection.
_jti_cache: _JTICache | None = None


def _get_jti_cache(max_ttl: int) -> _JTICache:
    global _jti_cache
    if _jti_cache is None or _jti_cache._max_ttl != max_ttl:
        _jti_cache = _JTICache(max_ttl)
    return _jti_cache


class AssertionService:
    """Verifies JWT Bearer assertions and exchanges them for opaque token pairs."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._jti_cache = _get_jti_cache(ctx.config.auth.assertion_max_ttl_seconds)

    async def verify_and_exchange(self, assertion: str) -> tuple[str, str]:
        """Verify a JWT assertion and return (access_token, refresh_token)."""
        try:
            unverified_header = jwt.get_unverified_header(assertion)
        except jwt.exceptions.DecodeError:
            raise InvalidGrantError(_INVALID) from None

        if unverified_header.get("alg") != "EdDSA":
            raise InvalidGrantError(_INVALID)

        try:
            unverified_payload = jwt.decode(
                assertion, options={"verify_signature": False}, algorithms=["EdDSA"]
            )
        except jwt.exceptions.DecodeError:
            raise InvalidGrantError(_INVALID) from None

        issuer = unverified_payload.get("iss")
        if not issuer or not isinstance(issuer, str):
            raise InvalidGrantError(_INVALID)

        # Phase 1: verify assertion + record audit in a single transaction.
        # This transaction is committed BEFORE issuing tokens so that
        # issue_pair's own run_in_transaction doesn't deadlock waiting for
        # this connection's write lock to release (SQLite single-writer).
        async with self._ctx.admin_db.transaction() as session:
            agent = await AgentRepository.get_by_id_for_update(session, issuer)

            if agent is None or agent.status != ActorStatus.ACTIVE or not agent.jwks:
                raise InvalidGrantError(_INVALID)

            public_key = resolve_agent_key(agent.jwks, unverified_header.get("kid"))
            if public_key is None:
                raise InvalidGrantError(_INVALID)

            try:
                payload = jwt.decode(
                    assertion,
                    public_key,
                    algorithms=["EdDSA"],
                    audience=self._expected_audience,
                    options={"require": ["exp", "iss", "aud", "jti"]},
                )
            except jwt.exceptions.InvalidTokenError:
                raise InvalidGrantError(_INVALID) from None

            self._validate_timing(payload)

            jti = payload.get("jti")
            if not jti or not self._jti_cache.check_and_insert(jti):
                raise InvalidGrantError(_INVALID)

            grants = await ActorScopeGrantRepository.list_for_actor(
                session, agent.id, actor_type=ActorType.AGENT
            )
            scopes = [g.scope for g in grants]

            await record_audit(
                session,
                action=AuditAction.LOGIN,
                target_type=AuditTargetType.SESSION,
                target_id=agent.id,
                actor_type=ActorType.AGENT,
                actor_id=agent.id,
                reason="jwt bearer assertion",
                origin=None,
            )

        # Phase 2: issue tokens (outside the transaction above).
        # issue_pair uses its own run_in_transaction internally.
        token_svc = TokenService(self._ctx)
        access_token, refresh_token = await token_svc.issue_pair(agent.id, ActorType.AGENT, scopes)

        return access_token, refresh_token

    @property
    def _expected_audience(self) -> str:
        base = self._ctx.config.auth.canonical_base_url
        return f"{base}/oauth/token"

    def _validate_timing(self, payload: dict[str, Any]) -> None:
        now = time.time()
        exp = payload.get("exp")
        max_ttl = self._ctx.config.auth.assertion_max_ttl_seconds
        if exp is None or exp <= now or exp > now + max_ttl:
            raise InvalidGrantError(_INVALID)
