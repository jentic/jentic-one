"""Hardened inbound-JWT verification over trusted-issuer JWKS (§08 E1).

The single source for *asymmetric* JWT verification across surfaces (the broker
edge today). Key resolution is delegated to PyJWT's :class:`PyJWKClient` — one
per trusted issuer — which fetches the issuer's published JWKS, caches keys by
``kid``, and re-fetches on an unknown ``kid`` bounded by ``lifespan`` (so a
rotated signing key is picked up without a restart).

Hardening rules enforced here:

- **Strict asymmetric alg allowlist** per issuer (``RS*``/``ES*``/``PS*``/
  ``EdDSA``). ``alg: none`` and **all HMAC algs are rejected** — an HS-signed
  token can't be validated against an RSA/EC public key (the RS↔HS key-confusion
  attack), and a token with no signature is never trusted.
- **``iss``** must match a configured trusted issuer (selected from the
  *unverified* claim, then re-asserted by ``jwt.decode``).
- **``aud``** must equal the broker's configured audience (when set).
- **``exp``/``nbf``/``iat``** validated with ``leeway`` clock-skew.

This module deliberately does **not** live in the broker: per §08 the JWKS/key
machinery is single-sourced under ``shared/auth`` (``test_jwks_single_source``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import jwt
import structlog
from jwt import PyJWKClient

from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.config import JwtVerificationConfig, TrustedIssuerConfig

logger = structlog.get_logger(__name__)


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


@dataclass
class TrustedIssuerVerifier:
    """Verify a self-contained JWT against a configured set of trusted issuers.

    Holds one :class:`PyJWKClient` per issuer (keys cached + rotation-aware). The
    ``iss`` claim selects the issuer; verification then pins to that issuer's
    JWKS, asymmetric alg allowlist, and the broker's expected audience.
    """

    config: JwtVerificationConfig
    _issuers: dict[str, _IssuerKeys] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # `lifespan` bounds how often an unknown-kid miss re-fetches the JWKS, so a
        # burst of unknown kids can't hammer the IdP; rotation is still picked up
        # within `lifespan`.
        self._issuers = {
            issuer.issuer: _IssuerKeys(
                config=issuer,
                client=PyJWKClient(
                    issuer.jwks_url,
                    cache_keys=True,
                    lifespan=300,
                ),
            )
            for issuer in self.config.trusted_issuers
        }

    def verify(self, token: str) -> dict[str, object]:
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

        # 3) Resolve the signing key by kid (rotation-aware fetch) and verify.
        try:
            signing_key = entry.client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError as exc:
            raise JwtVerificationError(f"jwt_key_unresolved: {exc}") from exc
        except jwt.InvalidTokenError as exc:
            raise JwtVerificationError(f"jwt_malformed: {exc}") from exc

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
