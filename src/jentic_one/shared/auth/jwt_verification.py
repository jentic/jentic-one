"""Hardened inbound-JWT verification over trusted-issuer JWKS.

The single source for *asymmetric* JWT verification across surfaces (the broker
edge today). Key resolution is delegated to PyJWT's :class:`PyJWKClient` — one
per trusted issuer — which fetches the issuer's published JWKS and caches the
JWK set for ``_JWKS_LIFESPAN_S`` without per-key indefinite caching. Withdrawn
signing keys stop resolving once ``lifespan`` expires, newly rotated signing
keys are picked up on a cache miss (bounded by the client's refresh cooldown),
and JWKS network fetches run off the event loop via :func:`asyncio.to_thread`.

Hardening rules enforced here:

- **Strict asymmetric alg allowlist** per issuer (``RS*``/``ES*``/``PS*``/
  ``EdDSA``). ``alg: none`` and **all HMAC algs are rejected** — an HS-signed
  token cannot be validated against an RSA/EC public key, and a token with no
  signature is never trusted.
- **``iss``** must match a configured trusted issuer (selected from the
  *unverified* claim, then re-asserted by ``jwt.decode``).
- **``aud``** must equal the broker's configured audience (when set).
- **``exp``/``nbf``/``iat``** validated with ``leeway`` clock-skew.

This module deliberately does **not** live in the broker: the JWKS/key
machinery is single-sourced under ``shared/auth`` (``test_jwks_single_source``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
import structlog
from jwt import PyJWKClient

from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.config import JwtVerificationConfig, TrustedIssuerConfig

logger = structlog.get_logger(__name__)

_JWKS_LIFESPAN_S: float = 300.0
_JWKS_FETCH_TIMEOUT_S: float = 5.0


class JwtVerificationError(TokenValidationError):
    """Raised when a JWT fails any hardened-verification check.

    Subclasses the shared :class:`TokenValidationError` so the broker edge can
    catch every token refusal with one typed clause and map it to a uniform
    401 (jentic-one#866).
    """


@dataclass
class _IssuerKeys:
    config: TrustedIssuerConfig
    client: PyJWKClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class TrustedIssuerVerifier:
    """Verify a self-contained JWT against a configured set of trusted issuers.

    Holds one :class:`PyJWKClient` per issuer with time-bounded JWK-set caching.
    The ``iss`` claim selects the issuer; verification then pins to that
    issuer's JWKS, asymmetric alg allowlist, and the broker's expected audience.
    """

    config: JwtVerificationConfig
    _issuers: dict[str, _IssuerKeys] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # `cache_keys=False` ensures every lookup consults the time-bounded
        # JWK-set cache (`lifespan`), so withdrawn keys stop resolving after
        # expiry while cooldown bounds unknown-kid refetches.
        self._issuers = {
            issuer.issuer: _IssuerKeys(
                config=issuer,
                client=PyJWKClient(
                    issuer.jwks_url,
                    cache_keys=False,
                    cache_jwk_set=True,
                    lifespan=_JWKS_LIFESPAN_S,
                    timeout=_JWKS_FETCH_TIMEOUT_S,
                ),
            )
            for issuer in self.config.trusted_issuers
        }

    @staticmethod
    def _lookup_cached_key(client: PyJWKClient, kid: str) -> Any | None:
        cache = client.jwk_set_cache
        if cache is None:
            return None
        jwk_set = cache.get()
        if jwk_set is None:
            return None
        for k in jwk_set.keys:
            if k.key_id == kid and k.public_key_use in ("sig", None):
                return k
        return None

    @staticmethod
    def _fetch_signing_key(client: PyJWKClient, kid: str) -> Any:
        cache = client.jwk_set_cache
        had_empty_cache = cache is not None and cache.get() is None
        prev_fetch = getattr(client, "_last_successful_fetch", None)
        if had_empty_cache:
            client._last_successful_fetch = time.monotonic()
        try:
            return client.get_signing_key(kid)
        except Exception:
            if had_empty_cache and cache is not None and cache.get() is None:
                client._last_successful_fetch = prev_fetch
            raise

    async def _resolve_key(self, entry: _IssuerKeys, kid: object) -> Any:
        if not isinstance(kid, str) or not kid:
            raise JwtVerificationError("jwt_kid_missing")

        cached = self._lookup_cached_key(entry.client, kid)
        if cached is not None:
            return cached

        async with entry.lock:
            cached = self._lookup_cached_key(entry.client, kid)
            if cached is not None:
                return cached
            try:
                return await asyncio.to_thread(self._fetch_signing_key, entry.client, kid)
            except jwt.PyJWKClientError as exc:
                raise JwtVerificationError(f"jwt_key_unresolved: {exc}") from exc
            except jwt.InvalidTokenError as exc:
                raise JwtVerificationError(f"jwt_malformed: {exc}") from exc

    async def verify(self, token: str) -> dict[str, object]:
        """Verify *token* and return its claims, or raise :class:`JwtVerificationError`."""
        # 1) Reject `alg: none` / HMAC before any key work, off the *unverified*
        #    header — a token claiming a symmetric alg never reaches key lookup.
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise JwtVerificationError(f"jwt_malformed: {exc}") from exc
        alg = header.get("alg")
        if not isinstance(alg, str) or alg.upper().startswith("HS") or alg.lower() == "none":
            raise JwtVerificationError(f"jwt_alg_not_allowed: {alg!r}")

        # 2) Select the trusted issuer from the unverified `iss` claim; we re-assert
        #    `iss` inside jwt.decode so this read can't be trusted on its own.
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.InvalidTokenError as exc:
            raise JwtVerificationError(f"jwt_malformed: {exc}") from exc
        iss = unverified.get("iss")
        if not isinstance(iss, str) or iss not in self._issuers:
            raise JwtVerificationError("jwt_untrusted_issuer")
        entry = self._issuers[iss]

        if alg not in entry.config.algorithms:
            raise JwtVerificationError(f"jwt_alg_not_allowed: {alg!r}")

        # 3) Resolve the signing key by kid (cached fast path or off-loop fetch) and verify.
        signing_key = await self._resolve_key(entry, header.get("kid"))

        require = ["exp", "iss"]
        if self.config.audience is not None:
            require.append("aud")
        try:
            claims: dict[str, object] = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(entry.config.algorithms),
                issuer=iss,
                audience=self.config.audience,
                leeway=self.config.leeway_s,
                options={"require": require},
            )
        except jwt.InvalidTokenError as exc:
            raise JwtVerificationError(f"jwt_invalid: {exc}") from exc
        except (OverflowError, ValueError) as exc:
            # PyJWT validates exp/nbf/iat via ``int(payload[...])``, so a signed
            # non-finite/absurd numeric claim (e.g. ``exp: inf``) raises
            # OverflowError/ValueError here rather than an InvalidTokenError.
            # This is the only decode that validates those claims — the earlier
            # unverified decode disables verification, so it can't hit this.
            raise JwtVerificationError(f"jwt_invalid: {exc}") from exc
        return claims
