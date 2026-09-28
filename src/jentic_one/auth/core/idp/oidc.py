"""OIDC identity provider adapters.

`OidcAdapter` is the generic, standards-compliant base. Provider-specific
adapters subclass it to supply well-known endpoint defaults and any
provider-specific claim handling (e.g. Google's `hd`). Construct the right one
for a config via the factory in `jentic_one.auth.core.idp.factory`.
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from jwt import PyJWKClient

from jentic_one.auth.core.idp.adapter import IdpClaims, TokenExchangeResult
from jentic_one.shared.config import IdpConfig

logger = structlog.get_logger(__name__)


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

    # ── Endpoint defaults (overridable by provider subclasses) ────────────────
    # Subclasses override the `_default_*_endpoint` hooks to plug in well-known
    # endpoints; explicit config always wins over any default.

    def _default_authorization_endpoint(self) -> str:
        return f"{self._config.issuer.rstrip('/')}/authorize"

    def _default_token_endpoint(self) -> str:
        return f"{self._config.issuer.rstrip('/')}/oauth/token"

    def _default_userinfo_endpoint(self) -> str:
        return f"{self._config.issuer.rstrip('/')}/userinfo"

    @property
    def _authorization_endpoint(self) -> str:
        return self._config.authorization_endpoint or self._default_authorization_endpoint()

    @property
    def _token_endpoint(self) -> str:
        return self._config.exchange_endpoint or self._default_token_endpoint()

    @property
    def _userinfo_endpoint(self) -> str:
        return self._config.userinfo_endpoint or self._default_userinfo_endpoint()

    def authorize_url(self, *, state: str, nonce: str, redirect_uri: str) -> str:
        """Build the OIDC authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._config.scopes),
            "state": state,
            "nonce": nonce,
        }
        return f"{self._authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, redirect_uri: str) -> TokenExchangeResult:
        """Exchange upstream code for tokens, then fetch userinfo.

        Returns userinfo plus the raw ID token, so provider subclasses that own
        a trust anchor for the issuer's signing keys can verify the ID token
        and prefer its claims where the provider does not surface them in
        userinfo (e.g. Google's ``hd``).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                self._token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret.get_secret_value(),
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("access_token", "")
            userinfo_resp = await client.get(
                self._userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo: dict[str, object] = userinfo_resp.json()

        id_token = token_data.get("id_token")
        id_token_claims = self._verify_id_token(id_token) if isinstance(id_token, str) else {}
        return TokenExchangeResult(userinfo=userinfo, id_token_claims=id_token_claims)

    def _verify_id_token(self, id_token: str) -> dict[str, object]:
        """Verify the upstream ID token and return its claims, or ``{}`` if unverified.

        The generic adapter has no configured trust anchor for arbitrary OIDC
        providers, so it declines to verify. Provider subclasses that ship
        well-known signing keys (e.g. Google) override this hook.
        """
        return {}

    def map_claims(self, exchange: TokenExchangeResult) -> IdpClaims:
        """Map standard OIDC claims to IdpClaims."""
        userinfo = exchange.userinfo
        return IdpClaims(
            external_subject=str(userinfo.get("sub", "")),
            email=str(userinfo.get("email", "")),
            first_name=str(userinfo.get("given_name", "")),
            last_name=str(userinfo.get("family_name", "")),
            email_verified=bool(userinfo.get("email_verified", False)),
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
    #: Google accepts both issuers on ID tokens.
    ACCEPTED_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
    #: Google signs ID tokens with RS256.
    SIGNING_ALGORITHMS = ("RS256",)

    #: Shared JWKS client — caches keys in-process, refetches on unknown ``kid``
    #: (bounded by ``lifespan``) so a Google key rotation is picked up without a
    #: restart. One instance per process is enough; PyJWKClient is thread-safe.
    _jwks_client: PyJWKClient = PyJWKClient(JWKS_URI, cache_keys=True, lifespan=300)

    def _default_authorization_endpoint(self) -> str:
        return self.AUTHORIZATION_ENDPOINT

    def _default_token_endpoint(self) -> str:
        return self.TOKEN_ENDPOINT

    def _default_userinfo_endpoint(self) -> str:
        return self.USERINFO_ENDPOINT

    def _verify_id_token(self, id_token: str) -> dict[str, object]:
        """Verify Google's ID token via the published JWKS and return its claims.

        On any verification failure we return ``{}`` and log — the caller then
        has no ``hd`` claim available, so a configured hard-gate rejects the
        login. That's the correct posture: an ID token we can't verify must not
        be trusted to satisfy a Workspace-only gate.
        """
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token)
            claims: dict[str, object] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=list(self.SIGNING_ALGORITHMS),
                audience=self._config.client_id,
                issuer=list(self.ACCEPTED_ISSUERS),
                options={"require": ["exp", "iss", "aud"]},
            )
            return claims
        except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
            logger.warning("google_id_token_verification_failed", error=str(exc))
            return {}

    def map_claims(self, exchange: TokenExchangeResult) -> IdpClaims:
        """Map Google claims, surfacing the `hd` (hosted-domain) claim.

        Google delivers ``hd`` on the ID token, not (reliably) on userinfo, so
        the verified ID-token claims are the source of truth; userinfo is only
        the fallback for backwards compatibility with test fixtures. ``hd`` is
        present only for Google Workspace accounts and is used by admission
        policies as a hard gate.
        """
        base = super().map_claims(exchange)
        hd = exchange.id_token_claims.get("hd") or exchange.userinfo.get("hd")
        return replace(base, hosted_domain=str(hd) if hd else None)
