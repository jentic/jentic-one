"""OIDC identity provider adapters.

`OidcAdapter` is the generic, standards-compliant base. Provider-specific
adapters subclass it to supply well-known endpoint defaults and any
provider-specific claim handling (e.g. Google's `hd`). Construct the right one
for a config via the factory in `jentic_one.auth.core.idp.factory`.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from jentic_one.auth.core.idp.adapter import IdpClaims, parse_email_verified
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.shared.config import IdpConfig

#: Asymmetric JWS algorithms permitted for upstream OIDC id_token verification.
#: Symmetric algorithms (HS*) and "none" are excluded so an upstream key cannot
#: be confused with an HMAC secret.
_ALLOWED_ID_TOKEN_ALGS: frozenset[str] = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)

#: Cache TTL for upstream JWKS documents (seconds). On unknown ``kid`` or key
#: verification failure against a cached JWKS, ``exchange_code`` forces one
#: immediate refresh to handle upstream key rotation cleanly.
_JWKS_CACHE_TTL_SECONDS: float = 300.0


class OidcAdapter:
    """Generic OIDC-compliant identity provider adapter.

    Implements the IdpAdapter protocol for any standards-compliant OIDC
    provider. Endpoints come from explicit config; when unset they're derived
    from the issuer. Provider-specific adapters (e.g. :class:`GoogleOidcAdapter`)
    subclass this to supply well-known endpoint defaults and claim handling.
    """

    def __init__(self, config: IdpConfig) -> None:
        self._config = config
        self._discovery: dict[str, object] | None = None
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0.0

    # ── Endpoint defaults (overridable by provider subclasses) ────────────────
    # Subclasses override the `_default_*_endpoint` hooks to plug in well-known
    # endpoints; explicit config always wins over any default.

    def _default_issuer(self) -> str:
        return self._config.issuer

    @property
    def issuer(self) -> str:
        return (self._config.issuer or self._default_issuer()).rstrip("/")

    def _default_authorization_endpoint(self) -> str:
        return f"{self.issuer}/authorize"

    def _default_token_endpoint(self) -> str:
        return f"{self.issuer}/oauth/token"

    def _default_userinfo_endpoint(self) -> str:
        return f"{self.issuer}/userinfo"

    def _default_jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @property
    def _authorization_endpoint(self) -> str:
        return self._config.authorization_endpoint or self._default_authorization_endpoint()

    @property
    def _token_endpoint(self) -> str:
        return self._config.exchange_endpoint or self._default_token_endpoint()

    @property
    def _userinfo_endpoint(self) -> str:
        return self._config.userinfo_endpoint or self._default_userinfo_endpoint()

    def authorize_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        code_challenge: str | None = None,
    ) -> str:
        """Build the OIDC authorization URL."""
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._config.scopes),
            "state": state,
            "nonce": nonce,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{self._authorization_endpoint}?{urlencode(params)}"

    async def _fetch_jwks(
        self, client: httpx.AsyncClient, *, force_refresh: bool = False
    ) -> dict[str, Any]:
        """Fetch the upstream provider's JWKS via OIDC discovery or default JWKS URI."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._jwks_cache is not None
            and (now - self._jwks_fetched_at) < _JWKS_CACHE_TTL_SECONDS
        ):
            return self._jwks_cache

        jwks_uri: str | None = None
        if not force_refresh and self._discovery is not None:
            cached_uri = self._discovery.get("jwks_uri")
            if isinstance(cached_uri, str) and cached_uri:
                jwks_uri = cached_uri

        if jwks_uri is None and self.issuer:
            discovery_url = f"{self.issuer}/.well-known/openid-configuration"
            try:
                disc_resp = await client.get(discovery_url)
                if disc_resp.status_code == 200:
                    disc_data = disc_resp.json()
                    if isinstance(disc_data, dict):
                        self._discovery = disc_data
                        discovered_uri = disc_data.get("jwks_uri")
                        if isinstance(discovered_uri, str) and discovered_uri:
                            jwks_uri = discovered_uri
            except (httpx.HTTPError, ValueError):
                pass

        if not jwks_uri:
            jwks_uri = self._default_jwks_uri()

        jwks_resp = await client.get(jwks_uri)
        jwks_resp.raise_for_status()
        try:
            jwks_data = jwks_resp.json()
        except ValueError as exc:
            raise InvalidGrantError("Upstream JWKS response is malformed") from exc
        if not isinstance(jwks_data, dict) or not isinstance(jwks_data.get("keys"), list):
            raise InvalidGrantError("Upstream JWKS response is malformed")
        self._jwks_cache = jwks_data
        self._jwks_fetched_at = time.monotonic()
        return jwks_data

    def _expected_issuer(self) -> str:
        """Resolve the expected upstream issuer from config or OIDC discovery."""
        if self.issuer:
            return self.issuer
        if self._discovery is not None:
            disc_iss = self._discovery.get("issuer")
            if isinstance(disc_iss, str) and disc_iss:
                return disc_iss.rstrip("/")
        return ""

    def _issuer_aliases(self) -> frozenset[str]:
        """Other ``iss`` spellings the provider documents for ``_expected_issuer()``."""
        return frozenset()

    def _verify_id_token(
        self,
        raw_id_token: str,
        jwks_data: dict[str, Any],
        *,
        expected_nonce: str | None,
    ) -> dict[str, Any]:
        """Verify the upstream id_token signature, standard claims, and nonce."""
        try:
            header = jwt.get_unverified_header(raw_id_token)
        except jwt.PyJWTError as exc:
            raise InvalidGrantError("Malformed upstream id_token header") from exc

        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in _ALLOWED_ID_TOKEN_ALGS:
            raise InvalidGrantError(f"Unsupported upstream id_token algorithm: {alg!r}")

        kid = header.get("kid")
        try:
            jwk_set = jwt.PyJWKSet.from_dict(jwks_data)
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise InvalidGrantError("Invalid upstream JWKS") from exc

        signing_key: Any = None
        if isinstance(kid, str) and kid:
            for jwk_key in jwk_set.keys:
                if jwk_key.key_id == kid:
                    signing_key = jwk_key.key
                    break
            if signing_key is None:
                raise InvalidGrantError("No matching key in upstream JWKS for kid")
        elif len(jwk_set.keys) == 1:
            signing_key = jwk_set.keys[0].key
        else:
            raise InvalidGrantError("Upstream id_token is missing kid header")

        required_claims = ["iss", "sub", "aud", "exp"]
        if expected_nonce is not None:
            if not expected_nonce:
                raise InvalidGrantError("Upstream id_token nonce mismatch")
            required_claims.append("nonce")

        try:
            claims: dict[str, Any] = jwt.decode(
                raw_id_token,
                signing_key,
                algorithms=[alg],
                audience=self._config.client_id,
                options={"require": required_claims, "verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise InvalidGrantError("Upstream id_token verification failed") from exc

        token_iss = claims.get("iss")
        if not isinstance(token_iss, str) or not token_iss:
            raise InvalidGrantError("Upstream id_token is missing iss claim")
        expected_iss = self._expected_issuer()
        normalised_iss = token_iss.rstrip("/")
        if not expected_iss or (
            normalised_iss != expected_iss and normalised_iss not in self._issuer_aliases()
        ):
            raise InvalidGrantError("Upstream id_token issuer mismatch")
        # Every accepted spelling keys identities under one canonical issuer.
        claims["iss"] = expected_iss

        token_sub = claims.get("sub")
        if not isinstance(token_sub, str) or not token_sub:
            raise InvalidGrantError("Upstream id_token is missing sub claim")

        if expected_nonce is not None:
            token_nonce = claims.get("nonce")
            if (
                not isinstance(token_nonce, str)
                or not token_nonce
                or not hmac.compare_digest(token_nonce, expected_nonce)
            ):
                raise InvalidGrantError("Upstream id_token nonce mismatch")

        return claims

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str,
        code_verifier: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, object]:
        """Exchange upstream code for tokens, verify id_token, then fetch userinfo."""
        token_form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret.get_secret_value(),
        }
        if code_verifier is not None:
            token_form["code_verifier"] = code_verifier

        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                self._token_endpoint,
                data=token_form,
            )
            token_resp.raise_for_status()
            try:
                token_data = token_resp.json()
            except ValueError as exc:
                raise InvalidGrantError("Upstream token response is malformed") from exc
            if not isinstance(token_data, dict):
                raise InvalidGrantError("Upstream token response is malformed")

            raw_id_token = token_data.get("id_token")
            if not isinstance(raw_id_token, str) or not raw_id_token:
                raise InvalidGrantError("Upstream token response is missing id_token")

            had_cached_jwks = (
                self._jwks_cache is not None
                and (time.monotonic() - self._jwks_fetched_at) < _JWKS_CACHE_TTL_SECONDS
            )
            jwks_data = await self._fetch_jwks(client)
            try:
                id_token_claims = self._verify_id_token(
                    raw_id_token,
                    jwks_data,
                    expected_nonce=nonce,
                )
            except InvalidGrantError:
                if not had_cached_jwks:
                    raise
                jwks_data = await self._fetch_jwks(client, force_refresh=True)
                id_token_claims = self._verify_id_token(
                    raw_id_token,
                    jwks_data,
                    expected_nonce=nonce,
                )

            access_token = token_data.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise InvalidGrantError("Upstream token response is missing access_token")
            userinfo_resp = await client.get(
                self._userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            try:
                userinfo = userinfo_resp.json()
            except ValueError as exc:
                raise InvalidGrantError("Upstream userinfo response is malformed") from exc
            if not isinstance(userinfo, dict):
                raise InvalidGrantError("Upstream userinfo response is malformed")

            userinfo_sub = userinfo.get("sub")
            if not isinstance(userinfo_sub, str) or not userinfo_sub:
                raise InvalidGrantError("Upstream userinfo response is missing sub")
            if not hmac.compare_digest(userinfo_sub, str(id_token_claims["sub"])):
                raise InvalidGrantError("Upstream userinfo sub does not match id_token sub")

            verified_iss = str(id_token_claims["iss"]).rstrip("/")
            userinfo_iss = userinfo.get("iss")
            if isinstance(userinfo_iss, str) and userinfo_iss:
                if userinfo_iss.rstrip("/") != verified_iss:
                    raise InvalidGrantError("Upstream userinfo iss does not match id_token iss")
            elif userinfo_iss is not None:
                raise InvalidGrantError("Upstream userinfo iss does not match id_token iss")

            if (
                "hd" in id_token_claims
                and "hd" in userinfo
                and str(userinfo["hd"]) != str(id_token_claims["hd"])
            ):
                raise InvalidGrantError("Upstream userinfo hd does not match id_token hd")

            merged: dict[str, object] = dict(userinfo)
            merged["iss"] = verified_iss
            if "hd" in id_token_claims:
                merged["hd"] = id_token_claims["hd"]
            return merged

    def map_claims(self, userinfo: dict[str, object]) -> IdpClaims:
        """Map standard OIDC claims to IdpClaims."""
        raw_iss = userinfo.get("iss")
        issuer = str(raw_iss).rstrip("/") if isinstance(raw_iss, str) and raw_iss else self.issuer
        return IdpClaims(
            external_subject=str(userinfo.get("sub", "")),
            email=str(userinfo.get("email", "")),
            first_name=str(userinfo.get("given_name", "")),
            last_name=str(userinfo.get("family_name", "")),
            email_verified=parse_email_verified(userinfo.get("email_verified")),
            issuer=issuer,
        )


class GoogleOidcAdapter(OidcAdapter):
    """OIDC adapter for Google (accounts.google.com / Google Workspace).

    Google is standards-compliant OIDC, so this only supplies the well-known
    endpoint defaults (a tenant need only provide client_id/client_secret) and
    surfaces Google's `hd` (hosted-domain) claim used by admission policies.
    Explicit `*_endpoint` config still overrides the defaults.
    """

    #: Well-known Google OIDC endpoints (used when config doesn't override them).
    ISSUER = "https://accounts.google.com"
    AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
    JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

    def _default_issuer(self) -> str:
        return self.ISSUER

    def _default_authorization_endpoint(self) -> str:
        return self.AUTHORIZATION_ENDPOINT

    def _default_token_endpoint(self) -> str:
        return self.TOKEN_ENDPOINT

    def _default_userinfo_endpoint(self) -> str:
        return self.USERINFO_ENDPOINT

    def _default_jwks_uri(self) -> str:
        return self.JWKS_URI

    def _issuer_aliases(self) -> frozenset[str]:
        # Google documents both ``https://accounts.google.com`` and the bare
        # ``accounts.google.com`` as the ``iss`` of its ID tokens.
        if self.issuer == self.ISSUER:
            return frozenset({"accounts.google.com"})
        return frozenset()

    def map_claims(self, userinfo: dict[str, object]) -> IdpClaims:
        """Map Google claims, surfacing the `hd` (hosted-domain) claim.

        `hd` is present only for Google Workspace accounts; it's None for
        consumer Google accounts. Admission policies use it as a hard gate.
        """
        base = super().map_claims(userinfo)
        hd = userinfo.get("hd")
        return replace(base, hosted_domain=str(hd) if hd else None)
