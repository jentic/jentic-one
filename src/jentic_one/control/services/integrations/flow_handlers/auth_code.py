"""Authorization-code (OAuth 2.0) handler for the connect-session state machine.

Owns the state-JWT signing done at confirm time, PKCE (RFC 7636) verifier
generation + persistence, and the server-side callback exchange. Between
confirm and callback the session sits in state=``polling``: the handler's
``poll`` reports pending, because completion is server-driven — the
human's browser lands on ``/credentials/oauth/callback`` after they
consent at the vendor.

Two OAuth-app sources are supported behind one seam (``SessionApp``):
* **Config** — writes the legacy ``oauth_client_credentials`` aux row at
  ``prepare`` time; ``complete_from_callback`` reads it back for the
  code exchange.
* **DB registration** — sets ``credentials.oauth_app_registration_id`` +
  ``credentials.owner_user_id`` at ``prepare`` time; skips the aux row.
  ``complete_from_callback`` dereferences the shared registration via
  its ``authorization_code_details`` extension.

Refresh path stays on ``DirectOAuth2Provider``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx
import structlog

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.repos.oauth_client_credential_repo import (
    OAuthClientCredentialRepository,
)
from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.control.services.integrations.errors import ConnectSessionServiceError
from jentic_one.control.services.integrations.flow_handlers.base import (
    AuthCodeBeginResult,
    BeginResult,
    SuccessTokens,
)
from jentic_one.control.services.integrations.flow_handlers.session_app import SessionApp
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import actor_type_from_id
from jentic_one.shared.models.credentials import StoredCredentialType
from jentic_one.shared.url_validation import validate_upstream_url

_logger = structlog.get_logger(__name__)


class AuthCodeExchangeError(ConnectSessionServiceError):
    """Raised when the vendor token endpoint refuses the authorization code."""


class RegistrationInactiveError(ConnectSessionServiceError):
    """Raised when the credential references a disabled ``oauth_app_registrations`` row."""


#: ``credential.provider`` value shared with the existing DirectOAuth2Provider —
#: keeps refresh dispatch on the same code path (no broker changes needed).
_PROVIDER_ID = "direct_oauth2"


def _pkce_challenge_from_verifier(verifier: str) -> str:
    """Derive the S256 code_challenge from a PKCE verifier (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class AuthCodeFlowHandler:
    """OAuth 2.0 authorization-code flow handler."""

    kind: ClassVar[str] = "authorization_code"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.OAUTH2_AUTHORIZATION_CODE
    provider_id: ClassVar[str] = _PROVIDER_ID

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def prepare(
        self,
        db_session: Any,
        *,
        credential_id: str,
        app: SessionApp,
        requested_scopes: list[str],
        created_by: str,
        owner_user_id: str | None = None,
    ) -> None:
        if app.registration_id is not None:
            # Shared-registration path: stamp the FK + owner on the credential
            # row and skip the legacy embedded aux write. All client material
            # is dereferenced through ``oauth_app_registrations`` at
            # complete-callback / refresh time.
            await CredentialRepository.set_oauth_app_registration(
                db_session,
                credential_id,
                registration_id=app.registration_id,
                owner_user_id=owner_user_id,
            )
            return

        # Legacy embedded path — copy the operator-config OAuth app into
        # ``oauth_client_credentials`` (same schema a manually-created
        # direct_oauth2 credential uses).
        assert app.authorize_url is not None and app.token_url is not None, (
            "config-source SessionApp for authorization_code must carry authorize_url + token_url"
        )
        assert app.client_secret_provider is not None, (
            "authorization_code is a confidential-client flow — client_secret required"
        )
        encrypted_client_secret = self._ctx.encryption.encrypt(app.client_secret_provider())
        await OAuthClientCredentialRepository.create(
            db_session,
            credential_id=credential_id,
            token_url=app.token_url,
            client_id=app.client_id,
            encrypted_client_secret=encrypted_client_secret,
            authorize_url=app.authorize_url,
            scope=" ".join(requested_scopes) if requested_scopes else None,
            created_by=created_by,
        )

    async def begin(
        self,
        row: ConnectSession,
        *,
        app: SessionApp,
        confirmed_scopes: list[str],
    ) -> BeginResult:
        assert app.authorize_url is not None, "authorize_url required for authorization_code"

        redirect_uri = self._redirect_uri()

        # PKCE (RFC 7636 §4.1): generate a fresh code_verifier and derive the
        # S256 code_challenge. Persist the verifier on the session row —
        # the state JWT transits the browser, so it is not a safe carrier
        # for the verifier. Older in-flight sessions that don't have a
        # verifier persisted will simply skip the ``code_verifier`` param at
        # exchange time (see ``complete_from_callback``), preserving legacy
        # behaviour for anything created before PKCE landed.
        code_verifier = secrets.token_urlsafe(64)[:96]
        code_challenge = _pkce_challenge_from_verifier(code_verifier)
        async with self._ctx.control_db.transaction() as session:
            await ConnectSessionRepository.update_fields(
                session, row.id, pkce_code_verifier=code_verifier
            )

        # Sign a state JWT that binds the callback back to this session +
        # actor. The ``sid`` claim is what the callback route uses to decide
        # "this belongs to a connect-session" rather than the standalone-
        # credential ConnectService path.
        connect_state = ConnectState(
            credential_id=row.credential_id,
            provider=_PROVIDER_ID,
            actor_id=row.initiator_actor_id,
            actor_type=actor_type_from_id(row.initiator_actor_id).value,
            issued_at=datetime.now(UTC),
            nonce=generate_nonce(),
            session_id=row.id,
        )
        state_secret = self._ctx.config.credentials.connect.state_secret.get_secret_value()
        ttl = self._ctx.config.credentials.connect.state_ttl_seconds
        signed_state = encode_state(state_secret, connect_state, ttl)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": app.client_id,
            "redirect_uri": redirect_uri,
            "state": signed_state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if confirmed_scopes:
            params["scope"] = " ".join(confirmed_scopes)
        authorize_url = f"{app.authorize_url}?{urlencode(params)}"

        return AuthCodeBeginResult(authorize_url=authorize_url)

    async def on_finalise(
        self,
        db_session: Any,
        *,
        credential_id: str,
    ) -> None:
        # No per-session transient state to clear — the oauth_client_credentials
        # row (legacy path) is the credential's permanent auth-code descriptor,
        # and the DB-registration path has no aux row at all.
        return

    async def complete_from_callback(
        self,
        row: ConnectSession,
        *,
        code: str,
    ) -> SuccessTokens:
        """Exchange ``code`` for tokens at the vendor's token endpoint.

        Reads client material through the credential's
        ``oauth_app_registration_id`` when set (shared-registration path); else
        from the ``oauth_client_credentials`` row seeded at ``prepare`` time
        (legacy embedded path). Includes the PKCE ``code_verifier`` from the
        session row when present.
        """
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
            if credential is None:
                raise AuthCodeExchangeError(
                    f"credential {row.credential_id!r} not found for callback exchange"
                )

            client_id: str
            token_url: str
            client_secret: str
            if credential.oauth_app_registration_id is not None:
                registration = await OAuthAppRegistrationRepository.get_by_id(
                    session, credential.oauth_app_registration_id
                )
                if registration is None or registration.authorization_code_details is None:
                    raise AuthCodeExchangeError(
                        "referenced oauth_app_registration is missing its "
                        "authorization_code_details extension"
                    )
                if not registration.is_active:
                    raise RegistrationInactiveError(
                        f"oauth_app_registration {registration.id!r} is inactive"
                    )
                client_id = registration.client_id
                token_url = registration.authorization_code_details.token_url
                client_secret = self._ctx.encryption.decrypt(
                    registration.authorization_code_details.encrypted_client_secret
                )
            else:
                occ = await OAuthClientCredentialRepository.get_by_credential(
                    session, row.credential_id
                )
                if occ is None:
                    raise AuthCodeExchangeError(
                        f"no oauth_client_credentials for credential {row.credential_id!r}"
                    )
                client_id = occ.client_id
                token_url = occ.token_url
                client_secret = self._ctx.encryption.decrypt(occ.encrypted_client_secret)

        redirect_uri = self._redirect_uri()

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        if row.pkce_code_verifier:
            payload["code_verifier"] = row.pkce_code_verifier
        # Defense-in-depth SSRF guard.
        try:
            safe_url = validate_upstream_url(token_url)
        except ValueError as exc:
            raise AuthCodeExchangeError(f"unsafe upstream URL: {exc}") from exc

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                safe_url,
                data=payload,
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            _logger.warning(
                "auth_code.token_exchange_failed",
                status=response.status_code,
                body_snippet=response.text[:200],
            )
            raise AuthCodeExchangeError(f"token exchange failed: HTTP {response.status_code}")
        try:
            data: dict[str, str] = response.json()
        except ValueError as exc:
            raise AuthCodeExchangeError("token endpoint returned non-JSON") from exc

        access_token = data.get("access_token")
        if not access_token:
            raise AuthCodeExchangeError("token endpoint returned no access_token")

        expires_in_raw = data.get("expires_in")
        expires_in: int | None
        try:
            expires_in = int(expires_in_raw) if expires_in_raw is not None else None
        except (TypeError, ValueError):
            expires_in = None

        scope = data.get("scope")
        return SuccessTokens(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_in=expires_in,
            scope=scope,
            # Auth-code: the server tells us what was granted.
            granted_scopes=scope.split() if scope else None,
        )

    def _redirect_uri(self) -> str:
        """Resolve the platform redirect URI from provider config.

        Reuses the ``credentials.providers.direct_oauth2.redirect_uri`` that
        the existing DirectOAuth2Provider registers with vendors, so the
        vendor OAuth app only ever needs one redirect_uri whitelisted for
        both entry points (connect-session flow and standalone credential
        connect).
        """
        provider_cfg = self._ctx.config.credentials.providers.get("direct_oauth2")
        if provider_cfg is None or not hasattr(provider_cfg, "redirect_uri"):
            raise RuntimeError(
                "credentials.providers.direct_oauth2.redirect_uri must be configured for "
                "the authorization_code connect flow"
            )
        return provider_cfg.redirect_uri
