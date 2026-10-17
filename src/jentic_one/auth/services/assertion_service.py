"""JWT Bearer assertion verification and token exchange service (RFC 7523)."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos.actor_permission_grant_repo import ActorPermissionGrantRepository
from jentic_one.admin.repos.agent_repo import AgentRepository
from jentic_one.admin.repos.assertion_jti_repo import AssertionJtiRepository
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth import resolve_agent_key
from jentic_one.shared.config import resolved_auth_base_url
from jentic_one.shared.context import Context
from jentic_one.shared.db.utils import db_now
from jentic_one.shared.models import ActorStatus, ActorType

_INVALID = "Assertion is invalid"

# Returned only after the assertion's signature has verified, so the caller has
# proven possession of the registered private key. A distinct detail is what
# lets the CLI's register/setup/wizard flows tell "wait for the operator to
# approve" apart from a genuinely rejected assertion (audience/signature) —
# with the ambiguous _INVALID for both, the approval wait aborted on first
# contact on self-hosted backends, which mint no claim tokens by default.
_PENDING = "Agent is not active yet (pending approval)"

#: Seconds a ``jti`` record outlives its assertion's ``exp``. The record's
#: lifetime runs on the admin database's clock while ``exp`` is checked on
#: each auth process's own clock, so a process whose clock lags the database
#: by up to this much still finds the record while it could accept the
#: assertion. Keep auth hosts on NTP; a lag beyond the grace re-opens a replay
#: window of that excess.
JTI_CLOCK_SKEW_GRACE_S = 120

#: Most expired ``jti`` records one exchange deletes. Each accepted exchange
#: adds one record, so pruning up to this many per exchange keeps the table
#: at its live set without one exchange paying for a large backlog.
JTI_PRUNE_BATCH = 50


def jti_digest(jti: str) -> str:
    """Hex SHA-256 of a ``jti`` claim: what the replay record stores instead of the claim.

    JSON lets a claim carry a lone UTF-16 surrogate (``"\\ud800"``), which
    strict UTF-8 cannot encode; ``surrogatepass`` keeps such a claim a
    distinct, hashable value instead of an unhandled error.
    """
    return hashlib.sha256(jti.encode("utf-8", "surrogatepass")).hexdigest()


class AssertionService:
    """Verifies JWT Bearer assertions and exchanges them for opaque token pairs."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def verify_and_exchange(
        self, assertion: str, *, request_base_url: str | None = None
    ) -> tuple[str, str, list[str]]:
        """Verify a JWT assertion and return (access_token, refresh_token, scopes).

        ``scopes`` is the agent's live ``actor_permission_grants`` set stamped on
        the minted pair, returned so the token endpoint can report the
        effective scope per RFC 6749 §5.1.

        ``request_base_url`` is the request-scoped base URL the discovery
        document advertises (``deployment_base_url``). Its ``/oauth/token`` is
        accepted as an audience alongside the request-less
        ``resolved_auth_base_url`` one, so a zero-config deployment reached on
        an origin other than its bind (port mapping, LAN address) accepts the
        ``token_endpoint`` it advertised. With a public base URL configured
        both resolve to the same value.
        """
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

            if agent is None or not agent.jwks:
                raise InvalidGrantError(_INVALID)

            public_key = resolve_agent_key(agent.jwks, unverified_header.get("kid"))
            if public_key is None:
                raise InvalidGrantError(_INVALID)

            try:
                payload = jwt.decode(
                    assertion,
                    public_key,
                    algorithms=["EdDSA"],
                    audience=self._expected_audiences(request_base_url),
                    options={"require": ["exp", "iss", "aud", "jti"]},
                )
            except jwt.exceptions.InvalidTokenError:
                raise InvalidGrantError(_INVALID) from None

            self._validate_timing(payload)

            # Status gate AFTER signature verification, deliberately:
            # 1. The distinct PENDING detail is only revealed to a caller who
            #    holds the registered private key, so it leaks nothing an
            #    unauthenticated prober could use (a bad signature still gets
            #    the generic _INVALID regardless of status).
            # 2. It is what makes the CLI's approval wait possible on backends
            #    without a claim-token minter: register/setup/wizard poll the
            #    token exchange until the operator approves, and need pending
            #    to be distinguishable from a hard assertion failure.
            # Other non-active statuses (rejected/disabled/archived) stay
            # deliberately generic: they are terminal, not waitable, and get
            # no dedicated probe signal.
            #
            # Checked BEFORE the jti replay record so approval polling (a fresh
            # jti per attempt) never writes records for exchanges that cannot
            # succeed.
            if agent.status == ActorStatus.PENDING:
                raise InvalidGrantError(_PENDING)
            if agent.status != ActorStatus.ACTIVE:
                raise InvalidGrantError(_INVALID)

            jti = payload.get("jti")
            if not jti or not isinstance(jti, str):
                raise InvalidGrantError(_INVALID)
            if not await self._claim_jti(session, issuer=agent.id, jti=jti, exp=payload["exp"]):
                raise InvalidGrantError(_INVALID)

            grants = await ActorPermissionGrantRepository.list_for_actor(
                session, agent.id, actor_type=ActorType.AGENT
            )
            scopes = [g.permission for g in grants]

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

        return access_token, refresh_token, scopes

    @staticmethod
    async def _claim_jti(session: AsyncSession, *, issuer: str, jti: str, exp: float) -> bool:
        """Record the assertion's ``jti`` as used for ``issuer``; ``False`` on a replay.

        Shared by every auth process through the admin database. The record
        is written in the verifying transaction, so an exchange rejected
        there (the transaction rolls back) leaves no record. That transaction
        commits before the token pair is minted in its own, so a failure
        minting the pair leaves the ``jti`` used: the client retries with a
        freshly signed assertion. It lives until ``exp`` plus
        :data:`JTI_CLOCK_SKEW_GRACE_S` on the database clock; after that the
        assertion fails the ``exp`` check everywhere, and a new assertion
        reusing the ``jti`` takes the record over. Expired records are pruned
        here in batches of :data:`JTI_PRUNE_BATCH`, after the claim: the
        prune skips rows other transactions hold and so never waits, which
        keeps a claim waiting on another exchange's prune from ever closing a
        lock cycle, and a replay is refused without paying for a prune.
        """
        now = await db_now(session)
        expires_at = datetime.fromtimestamp(exp, UTC) + timedelta(seconds=JTI_CLOCK_SKEW_GRACE_S)
        claimed = await AssertionJtiRepository.claim(
            session, issuer=issuer, jti_digest=jti_digest(jti), expires_at=expires_at, now=now
        )
        if claimed:
            await AssertionJtiRepository.delete_expired(session, now=now, limit=JTI_PRUNE_BATCH)
        return claimed

    def _expected_audiences(self, request_base_url: str | None) -> list[str]:
        # Normalized like deployment_base_url so a trailing slash in a
        # configured base URL can't break assertion validation.
        bases = [resolved_auth_base_url(self._ctx.config).rstrip("/")]
        if request_base_url and request_base_url.rstrip("/") not in bases:
            bases.append(request_base_url.rstrip("/"))
        return [f"{base}/oauth/token" for base in bases]

    def _validate_timing(self, payload: dict[str, Any]) -> None:
        now = time.time()
        exp = payload.get("exp")
        max_ttl = self._ctx.config.auth.assertion_max_ttl_seconds
        if exp is None or exp <= now or exp > now + max_ttl:
            raise InvalidGrantError(_INVALID)
