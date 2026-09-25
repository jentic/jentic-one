"""ConnectService — orchestrates the OAuth connect flow between router and provider."""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

import structlog

from jentic_one.control.repos import (
    ConnectNonceRepository,
    CredentialRepository,
    OAuthTokenRepository,
)
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectChallenge,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.state import (
    StateError,
    decode_state,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType

logger = structlog.get_logger()


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
        actor_id: str | None = None,
        actor_type: ActorType | None = None,
        redirect_uri: str | None = None,
    ) -> ConnectChallenge:
        """Load credential, resolve provider, initiate connect flow."""
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None:
                raise CredentialNotFoundError(credential_id)

            provider_name = credential.provider
            api = APIReference(
                vendor=credential.api_vendor,
                name=credential.api_name or "",
                version=credential.api_version or "",
            )

        provider = self._ctx.providers.get(provider_name)

        enriched_request = ConnectRequest(
            scopes=request.scopes,
            extra={
                **request.extra,
                "credential_id": credential_id,
                "actor_id": actor_id or "",
                "actor_type": str(actor_type) if actor_type else "",
            },
            redirect_uri=redirect_uri,
        )

        return await provider.begin_connect(self._ctx, api=api, request=enriched_request)

    async def complete(
        self,
        raw_state: str,
        callback: ConnectCallback,
    ) -> str:
        """Verify state, exchange code via provider, persist tokens. Returns credential_id."""
        state_secret = self._ctx.config.credentials.connect.state_secret.get_secret_value()

        try:
            state = decode_state(state_secret, raw_state)
        except StateError as exc:
            raise ConnectFlowError("Invalid or expired connect state") from exc

        encryption = self._ctx.encryption
        state_ttl = self._ctx.config.credentials.connect.state_ttl_seconds
        nonce_expires_at = state.issued_at + timedelta(seconds=state_ttl)
        # The connect flow binds the initiating subject into the signed state at
        # `begin`; a state without it cannot be attributed to a real actor.
        if state.actor_id is None:
            raise ConnectFlowError("Connect state is missing the initiating subject")
        created_by = state.actor_id

        actor_type = ActorType(state.actor_type) if state.actor_type else ActorType.USER
        try:
            async with self._ctx.control_db.transaction() as session:
                consumed = await ConnectNonceRepository.consume(
                    session,
                    nonce=state.nonce,
                    credential_id=state.credential_id,
                    expires_at=nonce_expires_at,
                    created_by=created_by,
                )
                if not consumed:
                    raise ConnectFlowError("Connect state already used")

            provider = self._ctx.providers.get(state.provider)
            result = await provider.complete_connect(self._ctx, state=state, callback=callback)

            async with self._ctx.control_db.transaction() as session:
                credential = await CredentialRepository.get_by_id(session, state.credential_id)
                if credential is None:
                    raise CredentialNotFoundError(state.credential_id)

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
