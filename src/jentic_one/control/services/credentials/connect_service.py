"""ConnectService — orchestrates the OAuth connect flow between router and provider."""

from __future__ import annotations

import datetime as dt

import structlog

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import (
    CredentialRepository,
    OAuthClientCredentialRepository,
    OAuthTokenRepository,
)
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
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
)
from jentic_one.control.services.credentials.token_endpoint import minted_at_current_origin
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType

logger = structlog.get_logger()

# Bound agents read for the re-authorize re-check: the permitted agent plus any
# other is enough to refuse.


class ConnectFlowError(Exception):
    """Raised when the connect flow fails for a domain reason."""


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
        """Load the caller's credential, resolve provider, initiate connect flow.

        A credential the caller cannot see is a 404. Visibility includes owner
        delegation (see the comment on the filter below).

        ``browser_binding`` names the browser that may finish the flow: its
        hash rides the signed state and the callback requires the matching
        cookie. Without it the state is unbound and every callback for it
        is refused.
        """
        # Read filter on purpose, not ``for_write``: an agent holding
        # ``owner:credentials:read`` may start a connect for its owner's
        # credential, which the provider's authorization step then completes.
        access_filters = build_access_filters(identity, Credential)
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)

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

        return await provider.begin_connect(self._ctx, api=api, request=enriched_request)

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

                # Always bump `updated_at` on the credential row when a connect
                # completes, regardless of whether `provider_account_ref` changed.
                # The SPA polls `GET /credentials/{id}` and uses `updated_at` (or a
                # `provider_account_ref` change) as the "the connection completed"
                # signal so it can close the OAuth popup. `direct_oauth2` stores
                # tokens in a sibling table and returns no `provider_account_ref`,
                # so without this explicit touch the credentials row never changes
                # and the SPA's polling loop times out, leaving the popup stuck on
                # `{"status":"connected"}`.
                credential.updated_at = dt.datetime.now(dt.UTC)
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
