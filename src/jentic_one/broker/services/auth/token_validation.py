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

**Trust contract (self-contained JWT path).** A trusted issuer vouches for the
claims it signs: the broker requires ``sub``, ``exp`` and ``actor_type`` and
refuses (uniform 401) any token missing them — it never *infers* a missing
claim (jentic-one#864). ``actor_type`` must be one of ``agent`` /
``service_account`` (``_ALLOWED_ACTOR_TYPES``); ``toolkit`` and ``user``
identities have DB-backed credential forms and cannot be asserted by a bare
signed claim (jentic-one#868). Every refusal is logged server-side at WARNING
with a static event name while the wire response stays uniform (jentic-one#874).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn, Protocol

import jwt
import structlog

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

# Algorithms we accept for the self-contained-JWT path. HS256 only for the
# minimal PR-A2 verifier; §08 widens/locks this down (and adds asymmetric/JWKS).
_ALLOWED_ALGS: frozenset[str] = frozenset({"HS256"})

# The self-contained-JWT path may only assert these actor types (jentic-one#868).
# TOOLKIT and USER identities have DB-backed credential forms (toolkit keys,
# opaque tokens) and must never enter the broker via a bare signed claim: a
# trusted issuer vouches for AGENT/SERVICE_ACCOUNT, nothing else.
_ALLOWED_ACTOR_TYPES: frozenset[ActorType] = frozenset({ActorType.AGENT, ActorType.SERVICE_ACCOUNT})


class TokenVerifier(Protocol):
    """A JWT verifier: signature + claim checks, returning the decoded claims.

    Both the dev HS256 :class:`JwtVerifier` and the hardened asymmetric
    ``TrustedIssuerVerifier`` (``shared/auth/jwt_verification``) satisfy this, so
    the dispatcher is agnostic to which is wired (§08 E1).
    """

    def verify(self, token: str) -> dict[str, object]: ...


class Claim(StrEnum):
    """JWT claim keys the broker reads — no bare claim strings in the verifier."""

    ISS = "iss"
    SUB = "sub"
    EXP = "exp"
    ACTOR_TYPE = "actor_type"
    SCOPES = "scopes"


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
        """Verify signature + expiry; raise ``TokenValidationError`` on failure."""
        # Pin the alg allowlist explicitly so an attacker can't downgrade to
        # ``alg:none``; PyJWT enforces ``exp`` when present by default.
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in _ALLOWED_ALGS:
            raise TokenValidationError("jwt_alg_not_allowed")
        try:
            claims: dict[str, object] = jwt.decode(
                token, self.secret, algorithms=list(_ALLOWED_ALGS)
            )
        except jwt.InvalidTokenError as exc:
            raise TokenValidationError(f"jwt_invalid: {exc}") from exc
        return claims


@dataclass(frozen=True, slots=True)
class JwtTokenValidator:
    """Validates a self-contained signed JWT into an ``Identity`` (no DB lookup)."""

    verifier: TokenVerifier

    def _refuse(self, event: str, **fields: object) -> NoReturn:
        """Log a refusal cause server-side, then raise the uniform typed error.

        The wire response stays a uniform 401 (no oracle for forgers); the
        static ``event`` name and structured ``fields`` are the *only* record
        an operator gets of *why* a token was refused (jentic-one#874). Never
        pass the raw token or a full claim set here — attacker-influenced
        values are truncated by the caller.
        """
        logger.warning(event, **fields)
        raise TokenValidationError(event)

    async def validate(self, token: str) -> Identity:
        try:
            claims = self.verifier.verify(token)
        except TokenValidationError as exc:
            # The verifier already carries the specific reason; surface it
            # centrally so both verifiers behind the protocol get refusal
            # logging without each duplicating it.
            logger.warning("jwt_refused", reason=str(exc)[:128])
            raise

        iss = claims.get(Claim.ISS)
        sub = claims.get(Claim.SUB)
        exp = claims.get(Claim.EXP)
        if not isinstance(sub, str) or not isinstance(exp, int | float):
            self._refuse("jwt_missing_required_claims", iss=iss)

        # Fail closed on actor_type: a trusted issuer must *declare* it — we
        # never default a missing claim to AGENT (jentic-one#864). Truncate the
        # attacker-controlled value before logging.
        actor_type_raw = claims.get(Claim.ACTOR_TYPE)
        if actor_type_raw is None:
            self._refuse("jwt_actor_type_missing", iss=iss, sub=sub)
        try:
            actor_type = ActorType(str(actor_type_raw))
        except ValueError:
            self._refuse(
                "jwt_actor_type_unknown",
                iss=iss,
                sub=sub,
                actor_type=str(actor_type_raw)[:64],
            )
        if actor_type not in _ALLOWED_ACTOR_TYPES:
            # A signed claim can't mint a toolkit/user identity — those have
            # DB-backed credential forms (jentic-one#868).
            self._refuse(
                "jwt_actor_type_not_allowed",
                iss=iss,
                sub=sub,
                actor_type=actor_type.value,
            )

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
