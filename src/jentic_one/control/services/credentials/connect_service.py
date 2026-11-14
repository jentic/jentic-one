"""ConnectService — orchestrates the OAuth connect flow between router and provider."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import structlog

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.repos import (
    ConnectNonceRepository,
    CredentialRepository,
    OAuthClientCredentialRepository,
    OAuthTokenRepository,
)
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.credentials.errors import (
    ConnectRequiresReviewError,
    ConnectScopeWideningError,
    CredentialNotFoundError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectChallenge,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.state import (
    BrowserBindingClaim,
    StateError,
    consume_callback_state,
    decode_state,
)
from jentic_one.control.services.credentials.token_endpoint import minted_at_current_origin
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType

logger = structlog.get_logger()


class ConnectFlowError(Exception):
    """Raised when the connect flow fails for a domain reason."""


def _stored_scopes(
    credential: Credential, registration: OAuthAppRegistration | None = None
) -> set[str] | None:
    """Return the stored scope set for a device or authorization-code credential."""
    dfc = credential.device_authorization_credential
    if dfc is not None:
        scopes = set(dfc.requested_scopes or ()) | set(dfc.granted_scopes or ())
        if credential.oauth_token is not None and credential.oauth_token.scope:
            scopes |= set(credential.oauth_token.scope.split())
        return scopes
    occ = credential.oauth_client_credential
    if occ is not None:
        if occ.scope:
            return set(occ.scope.split())
        if credential.oauth_token is not None and credential.oauth_token.scope:
            return set(credential.oauth_token.scope.split())
        return set()
    if credential.oauth_app_registration_id is not None:
        if credential.oauth_token is not None and credential.oauth_token.scope:
            return set(credential.oauth_token.scope.split())
        if registration is not None:
            if registration.authorization_code_details is not None:
                return set(registration.authorization_code_details.default_scopes or ())
            if registration.device_authorization_details is not None:
                return set(registration.device_authorization_details.default_scopes or ())
        return set()
    return None


class ConnectService:
    """Thin orchestration for the credential connect flow."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def begin(
        self,
        credential_id: str,
        request: ConnectRequest,
        *,
        identity: Identity,
        redirect_uri: str | None = None,
        browser_binding: BrowserBindingClaim | None = None,
    ) -> ConnectChallenge:
        """Load the caller's write-owned credential, resolve provider, initiate connect flow.

        Connecting overwrites the credential's stored tokens, so it requires
        write ownership (``for_write=True``). An agent that can only read the
        credential through owner delegation receives
        :class:`ConnectRequiresReviewError` (403 ``connect_requires_review``)
        pointing at the reviewed connect-session flow; any other miss raises
        :class:`CredentialNotFoundError` (404). Requested scopes may only
        narrow the stored scope set; widening raises
        :class:`ConnectScopeWideningError` (422 ``scope_widening_requires_review``).

        ``browser_binding`` names the browser that may finish the flow: its
        hash rides the signed state and the callback requires the matching
        cookie. Without it the state is unbound and every callback for it
        is refused.
        """
        access_filters = build_access_filters(identity, Credential, for_write=True)
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                if identity.actor_type == ActorType.AGENT:
                    read_filters = build_access_filters(identity, Credential)
                    visible = await CredentialRepository.get_by_id(
                        session, credential_id, filters=read_filters
                    )
                    if visible is not None:
                        raise ConnectRequiresReviewError(credential_id)
                raise CredentialNotFoundError(credential_id)

            registration: OAuthAppRegistration | None = None
            if (
                request.scopes
                and credential.device_authorization_credential is None
                and credential.oauth_client_credential is None
                and credential.oauth_app_registration_id is not None
            ):
                registration = await OAuthAppRegistrationRepository.get_by_id(
                    session, credential.oauth_app_registration_id
                )
            stored_scopes = _stored_scopes(credential, registration)
            if request.scopes and stored_scopes is not None:
                widened = [scope for scope in request.scopes if scope not in stored_scopes]
                if widened:
                    raise ConnectScopeWideningError(credential_id, widened)

            provider_name = credential.provider
            api = APIReference(
                vendor=credential.api_vendor,
                name=credential.api_name or "",
                version=credential.api_version or "",
            )

        provider = self._ctx.providers.get(provider_name)

        extra = dict(request.extra)
        extra.update(
            credential_id=credential_id,
            provider_id=provider_name,
            actor_id=identity.sub,
            actor_type=str(identity.actor_type),
        )
        enriched_request = ConnectRequest(
            scopes=request.scopes,
            extra=extra,
            redirect_uri=redirect_uri,
            browser_binding_hash=browser_binding.digest if browser_binding else None,
            browser_host=browser_binding.host if browser_binding else None,
        )

        challenge = await provider.begin_connect(self._ctx, api=api, request=enriched_request)
        if challenge.kind == "authorization_code":
            state = decode_state(
                self._ctx.config.credentials.connect.state_secret.get_secret_value(),
                challenge.state,
            )
            if state.session_id is None:
                challenge.flow_id = state.nonce
        return challenge

    async def get_flow_status(
        self, credential_id: str, flow_id: str, *, identity: Identity
    ) -> Literal["pending", "connected"]:
        """Return only the requested visible flow's terminal completion state."""
        access_filters = build_access_filters(identity, Credential)
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)
            connected = await ConnectNonceRepository.is_completed(
                session, nonce=flow_id, credential_id=credential_id
            )
        return "connected" if connected else "pending"

    async def complete(
        self,
        raw_state: str,
        callback: ConnectCallback,
        *,
        browser_binding: str | None,
    ) -> str:
        """Verify state, exchange code via provider, persist tokens. Returns credential_id.

        ``browser_binding`` is the binding cookie the callback carried.
        """
        # Shared prologue with ``ConnectSessionService.complete_from_callback``
        # so replay protection lives in exactly one place. ``StateError``
        # covers every failure mode (expired / invalid / missing-actor /
        # nonce already consumed); we surface the taxonomy back to the
        # router via ``ConnectFlowError`` for the standalone path.
        try:
            state = await consume_callback_state(
                self._ctx, raw_state, browser_binding=browser_binding
            )
        except StateError as exc:
            raise ConnectFlowError(str(exc)) from exc

        encryption = self._ctx.encryption
        created_by = state.actor_id
        # ``consume_callback_state`` guarantees ``actor_id`` is populated;
        # narrow the type for mypy.
        assert created_by is not None

        try:
            actor_type = ActorType(state.actor_type) if state.actor_type else ActorType.USER
        except ValueError as exc:
            # A state signed for a retired actor type (e.g. ``service_account``,
            # theme-8 Phase 4): that actor can no longer own a credential.
            raise ConnectFlowError(f"Unsupported actor_type in state: {state.actor_type}") from exc
        try:
            async with self._ctx.control_db.session() as session:
                credential = await CredentialRepository.get_by_id(session, state.credential_id)
                if credential is None:
                    raise CredentialNotFoundError(state.credential_id)
                if credential.provider != state.provider:
                    raise ConnectFlowError(
                        f"State provider {state.provider!r} does not match "
                        f"credential provider {credential.provider!r}"
                    )

            provider = self._ctx.providers.get(state.provider)
            result = await provider.complete_connect(self._ctx, state=state, callback=callback)

            async with self._ctx.control_db.transaction() as session:
                # Same lock as the broker refresh and a token_url edit, then
                # re-check the endpoint: a cross-origin edit that committed
                # while the code was being exchanged has moved the credential
                # away from the origin this grant was minted at, and storing
                # it would hand its refresh token to the new origin.
                await OAuthTokenRepository.acquire_refresh_lock(session, state.credential_id)
                credential = await CredentialRepository.get_by_id(session, state.credential_id)
                if credential is None:
                    raise CredentialNotFoundError(state.credential_id)
                if credential.provider != state.provider:
                    raise ConnectFlowError(
                        f"State provider {state.provider!r} does not match "
                        f"credential provider {credential.provider!r}"
                    )
                current_token_url = await OAuthClientCredentialRepository.current_token_url(
                    session, credential
                )
                if not minted_at_current_origin(result.token_url, current_token_url):
                    logger.warning(
                        "oauth_connect_discarded_token_url_moved",
                        credential_id=state.credential_id,
                    )
                    raise ConnectFlowError(
                        "the credential's token_url moved to another origin while connecting; "
                        "reconnect the credential"
                    )

                existing_token = await OAuthTokenRepository.get_by_credential(
                    session, state.credential_id
                )

                if result.access_token:
                    encrypted_access = encryption.encrypt(result.access_token)
                    encrypted_refresh = (
                        encryption.encrypt(result.refresh_token) if result.refresh_token else None
                    )

                    if existing_token is not None:
                        await OAuthTokenRepository.update_tokens(
                            session,
                            state.credential_id,
                            encrypted_access_token=encrypted_access,
                            encrypted_refresh_token=encrypted_refresh,
                            expires_at=result.expires_at,
                            scope=result.scope,
                        )
                    else:
                        await OAuthTokenRepository.create(
                            session,
                            credential_id=state.credential_id,
                            encrypted_access_token=encrypted_access,
                            encrypted_refresh_token=encrypted_refresh,
                            expires_at=result.expires_at,
                            scope=result.scope,
                            created_by=created_by,
                        )
                elif result.provider_account_ref:
                    pass
                else:
                    raise ConnectFlowError(
                        "Provider returned neither access_token nor provider_account_ref"
                    )

                if result.provider_account_ref is not None:
                    credential.provider_account_ref = result.provider_account_ref

                # Keep the credential snapshot fresh for ordinary cache consumers.
                # Connect completion itself is correlated through the nonce below,
                # not inferred from this row's timestamp or account reference.
                credential.updated_at = dt.datetime.now(dt.UTC)
                await ConnectNonceRepository.mark_completed(
                    session,
                    nonce=state.nonce,
                    credential_id=state.credential_id,
                )
                await session.flush()
        except Exception:
            await self._emit_connect_telemetry(
                type=EventType.CREDENTIAL_CONNECTION_FAILED,
                summary=f"Credential {state.credential_id} connection failed",
                created_by=created_by,
                actor_type=actor_type,
            )
            raise

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.CREDENTIAL,
            target_id=state.credential_id,
            actor_type=actor_type,
            actor_id=created_by,
            after={"connected": True, "provider": state.provider},
            reason="oauth connect completed",
            origin=None,
        )
        await self._emit_connect_telemetry(
            type=EventType.CREDENTIAL_CONNECTED,
            summary=f"Credential {state.credential_id} connected",
            created_by=created_by,
            actor_type=actor_type,
        )
        return state.credential_id

    async def _emit_connect_telemetry(
        self, *, type: str, summary: str, created_by: str, actor_type: ActorType
    ) -> None:
        """Emit a connect lifecycle event on the admin DB (best-effort).

        The connect flow writes to the control DB; telemetry lives in the admin
        DB, so we open a short admin transaction for the (best-effort) write.
        """
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=type,
                    severity=EventSeverity.INFO,
                    summary=summary,
                    created_by=created_by,
                    actor_id=created_by,
                    actor_type=actor_type.value,
                )
        except Exception:
            logger.warning("telemetry_emit_failed", event_type=type, exc_info=True)
