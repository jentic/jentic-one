"""Dual-token validation for the broker edge — opaque tokens **and** signed JWTs.

Auth **service** layer (§00 layering): the web ``deps.py`` dependency calls a
single ``DualTokenValidator`` stored on ``app.state.broker_token_validator``; the
dispatcher routes self-contained JWTs (verified by signature, no DB lookup) to
``JwtTokenValidator`` and opaque tokens to the existing
``CachedTokenValidator`` (DB-backed, short-TTL cached).

The JWT path routes self-contained JWTs to the configured ``TokenVerifier``: the
dev HS256 :class:`JwtVerifier` (shared-secret) or the hardened asymmetric
``TrustedIssuerVerifier`` (JWKS rotation, ``iss``/``aud``/``nbf``, strict alg
allowlist, RS↔HS confusion defence — ``shared/auth/jwt_verification``, §08 E1),
selected by ``install_broker_auth`` from config. Opaque tokens go to the
existing ``CachedTokenValidator`` (DB-backed, short-TTL cached).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import jwt
import structlog

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

# Algorithms we accept for the self-contained-JWT path. HS256 only for the
# minimal PR-A2 verifier; §08 widens/locks this down (and adds asymmetric/JWKS).
_ALLOWED_ALGS: frozenset[str] = frozenset({"HS256"})


class TokenVerifier(Protocol):
    """A JWT verifier: signature + claim checks, returning the decoded claims.

    Both the dev HS256 :class:`JwtVerifier` and the hardened asymmetric
    ``TrustedIssuerVerifier`` (``shared/auth/jwt_verification``) satisfy this, so
    the dispatcher is agnostic to which is wired (§08 E1).
    """

    def verify(self, token: str) -> dict[str, object]: ...


class Claim(StrEnum):
    """JWT claim keys the broker reads — no bare claim strings in the verifier."""

    SUB = "sub"
    EXP = "exp"
    ISS = "iss"
    ACTOR_TYPE = "actor_type"
    SCOPES = "scopes"


# Dedup for the missing-actor_type warning: one line per (iss, sub) instead of
# one per request, so a steady issuer that omits the claim doesn't flood the
# logs. Bounded — reset wholesale rather than tracking recency.
_MISSING_ACTOR_TYPE_SEEN: set[tuple[str, str]] = set()
_MISSING_ACTOR_TYPE_SEEN_MAX = 1024


def looks_like_jwt(token: str) -> bool:
    """Heuristic: a compact JWS is three non-empty base64url segments.

    Cheap structural check used only to *route* the token; the actual decision to
    trust it is the signature verification in ``JwtTokenValidator``.
    """
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


@dataclass(frozen=True, slots=True)
class JwtVerifier:
    """Minimal HS256 verifier (signature + ``exp``). TODO(§08): harden."""

    secret: str

    def verify(self, token: str) -> dict[str, object]:
        """Verify signature + expiry; raise ``ValueError`` on any failure."""
        # Pin the alg allowlist explicitly so an attacker can't downgrade to
        # ``alg:none``; PyJWT enforces ``exp`` when present by default.
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in _ALLOWED_ALGS:
            raise ValueError("jwt_alg_not_allowed")
        try:
            claims: dict[str, object] = jwt.decode(
                token, self.secret, algorithms=list(_ALLOWED_ALGS)
            )
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"jwt_invalid: {exc}") from exc
        return claims


@dataclass(frozen=True, slots=True)
class JwtTokenValidator:
    """Validates a self-contained signed JWT into an ``Identity`` (no DB lookup).

    ``actor_type`` claim handling (#864): a missing claim defaults to the
    least-privileged AGENT (external trusted issuers have no claims contract
    requiring it) and is logged; an unrecognised value is refused outright.
    """

    verifier: TokenVerifier

    async def validate(self, token: str) -> Identity:
        """Verify the JWT and map its claims to an ``Identity`` (fails closed)."""
        claims = self.verifier.verify(token)
        sub = claims.get(Claim.SUB)
        exp = claims.get(Claim.EXP)
        if not isinstance(sub, str) or not isinstance(exp, int | float):
            raise ValueError("jwt_missing_required_claims")
        iss = str(claims.get(Claim.ISS, ""))

        actor_type_raw = claims.get(Claim.ACTOR_TYPE)
        if actor_type_raw is None:
            # Unlike the shared admin gate (#862), the broker's JWT producers
            # include *external* trusted issuers with no published claims
            # contract requiring actor_type, so a missing claim keeps the
            # least-privileged AGENT default. Log it (deduped per issuer/sub)
            # so operators can see which issuers omit the claim before any
            # future hard refusal. Same event name as the shared gate so both
            # aggregate together; ``outcome`` tells the two behaviours apart.
            if (iss, sub) not in _MISSING_ACTOR_TYPE_SEEN:
                if len(_MISSING_ACTOR_TYPE_SEEN) >= _MISSING_ACTOR_TYPE_SEEN_MAX:
                    _MISSING_ACTOR_TYPE_SEEN.clear()
                _MISSING_ACTOR_TYPE_SEEN.add((iss, sub))
                logger.warning(
                    "jwt_actor_type_missing",
                    sub=sub,
                    iss=iss,
                    outcome="defaulted_to_agent",
                )
            actor_type = ActorType.AGENT
        else:
            try:
                actor_type = ActorType(str(actor_type_raw))
            except ValueError as exc:
                # An unrecognised value is a malformed token, not a contract
                # gap — refuse it deliberately (#864) with the same error
                # shape as any other invalid JWT instead of letting the enum
                # constructor's ValueError escape by accident. The claim value
                # is attacker-influenced: truncate it before logging.
                logger.warning(
                    "jwt_actor_type_unknown",
                    sub=sub,
                    iss=iss,
                    actor_type=str(actor_type_raw)[:64],
                )
                raise ValueError("jwt_actor_type_unknown") from exc

        scopes_raw = claims.get(Claim.SCOPES, [])
        permissions = [str(s) for s in scopes_raw] if isinstance(scopes_raw, list) else []

        return Identity(
            sub=sub,
            actor_type=actor_type,
            permissions=permissions,
            expires_at=datetime.fromtimestamp(float(exp), tz=UTC),
            active=True,
        )


@dataclass(frozen=True, slots=True)
class DualTokenValidator:
    """Routes JWTs to the verifier and opaque tokens to the cached DB validator."""

    opaque: CachedTokenValidator
    jwt: JwtTokenValidator | None = None

    async def validate(self, token: str) -> Identity:
        if self.jwt is not None and looks_like_jwt(token):
            return await self.jwt.validate(token)
        return await self.opaque.validate(token)


def _is_api_key(value: str) -> bool:
    """Check whether a credential string is a prefixed API key (jak_ or sak_)."""
    return value.startswith("jak_") or value.startswith("sak_")


def _is_toolkit_key(value: str) -> bool:
    """Check whether a credential string is a toolkit key (jntc_live_)."""
    return value.startswith("jntc_live_")


@dataclass(frozen=True, slots=True)
class CompositeTokenValidator:
    """Routes toolkit keys, API keys, JWTs, and opaque tokens to the right validator.

    Dispatch order (most-specific prefix first):
    1. ``jntc_live_`` prefix → ToolkitKeyResolver (toolkit-scoped identity)
    2. ``jak_`` / ``sak_`` prefix → ApiKeyResolver (via CachedTokenValidator)
    3. Three-segment dot-separated → JWT verifier
    4. Everything else → opaque token CachedTokenValidator
    """

    opaque: CachedTokenValidator
    api_key: CachedTokenValidator
    toolkit_key: CachedTokenValidator
    jwt: JwtTokenValidator | None = None

    async def validate(self, token: str) -> Identity:
        if _is_toolkit_key(token):
            return await self.toolkit_key.validate(token)
        if _is_api_key(token):
            return await self.api_key.validate(token)
        if self.jwt is not None and looks_like_jwt(token):
            return await self.jwt.validate(token)
        return await self.opaque.validate(token)
