"""Device-flow (RFC 8628) handler for the connect-session state machine.

Owns the ``device_authorization_credentials`` aux table, the RFC 8628 device-auth
request at confirm time, and the poll loop against the vendor's token
endpoint. Bound-scope readback lives on the shared ``oauth_token.scope``
column, written at finalise time — the service reads it flow-agnostically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.services.integrations import device_authorization
from jentic_one.control.services.integrations.flow_handlers.base import (
    BeginResult,
    DeviceAuthorizationBeginResult,
    StatusReport,
    SuccessTokens,
)
from jentic_one.shared.config import VendorDeviceAuthorizationFlowConfig, VendorFlowConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import StoredCredentialType


class DeviceAuthorizationHandler:
    """RFC 8628 device-flow handler."""

    kind: ClassVar[str] = "device_authorization"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.OAUTH2_DEVICE_CODE
    provider_id: ClassVar[str] = "device_authorization"

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def prepare(
        self,
        db_session: AsyncSession,
        *,
        credential_id: str,
        flow: VendorFlowConfig,
        requested_scopes: list[str],
        created_by: str,
    ) -> None:
        # ``requested_scopes`` lives on the connect_sessions row now
        # (flow-agnostic); pass an empty list to the aux row so the column
        # sees no stale copy. Left non-NULL to preserve the schema shape.
        assert isinstance(flow, VendorDeviceAuthorizationFlowConfig)
        await DeviceAuthorizationCredentialRepository.create(
            db_session,
            credential_id=credential_id,
            client_id=flow.client_id,
            token_url=flow.token_endpoint,
            authorization_endpoint=flow.authorization_endpoint,
            requested_scopes=[],
            created_by=created_by,
        )

    async def begin(
        self,
        row: ConnectSession,
        *,
        flow: VendorFlowConfig,
        confirmed_scopes: list[str],
    ) -> BeginResult:
        assert isinstance(flow, VendorDeviceAuthorizationFlowConfig)

        # Talk to the vendor OUTSIDE the DB transaction — network latency
        # has no business holding a control-DB row lock.
        result = await device_authorization.begin_device_authorization(
            authorization_endpoint=flow.authorization_endpoint,
            client_id=flow.client_id,
            scopes=confirmed_scopes,
        )
        encrypted_device_code = self._ctx.encryption.encrypt(result.device_code)
        expires_at = datetime.now(UTC) + timedelta(seconds=result.expires_in)

        async with self._ctx.control_db.transaction() as session:
            await DeviceAuthorizationCredentialRepository.set_transient_state(
                session,
                row.credential_id,
                encrypted_device_code=encrypted_device_code,
                user_code=result.user_code,
                verification_uri=result.verification_uri,
                verification_uri_complete=result.verification_uri_complete,
                poll_interval_seconds=result.interval,
                device_code_expires_at=expires_at,
                granted_scopes=confirmed_scopes,
            )

        return DeviceAuthorizationBeginResult(
            user_code=result.user_code,
            verification_uri=result.verification_uri,
            verification_uri_complete=result.verification_uri_complete,
            poll_interval_seconds=result.interval,
        )

    async def advance(self, credential_id: str) -> StatusReport:
        """Drive one poll tick against the vendor.

        Called **only** by ``ConnectPollScanner`` — never from a request
        handler. Client-facing ``/status`` reads stored state and doesn't
        touch the vendor. One code path, one poll driver.

        Takes a bare ``credential_id`` (not a ``ConnectSession``) so the
        method is entrypoint-agnostic: both the session flow and the
        raw-credential connect flow write ``device_authorization_credentials`` and
        share this poll body.

        Enforces the vendor-supplied device-code TTL and the RFC 8628 poll
        interval as a rate limit. On any non-RFC-8628 error surfaced by
        ``poll_device_authorization`` (403, malformed body, unexpected 4xx/5xx),
        the outcome is terminal-failed with ``vendor_forbidden`` /
        ``vendor_error`` — no retry, no exponential backoff. Operator-
        visible failures beat silent time-wasters.
        """
        async with self._ctx.control_db.session() as read_session:
            dfc = await DeviceAuthorizationCredentialRepository.get_by_credential(
                read_session, credential_id
            )

        # Device-code TTL (vendor-supplied) — flow-specific guard.
        if (
            dfc is not None
            and dfc.device_code_expires_at is not None
            and datetime.now(UTC) >= dfc.device_code_expires_at
        ):
            return StatusReport(
                kind="expired",
                error_code="device_code_expired",
                terminal_detail="device_code expired",
            )

        # Rate limit — skip the vendor call if we polled within the interval.
        if not _should_poll_now(dfc):
            return StatusReport(kind="pending")

        try:
            return await self._poll_vendor(credential_id, dfc)
        except device_authorization.DeviceAuthorizationUpstreamError as exc:
            # Non-retryable — any HTTP status the RFC 8628 mapper couldn't
            # recognise (403 revoked app, 401 misconfigured client_id,
            # 5xx surge that didn't clear, malformed body). Fail loudly
            # rather than time-waste up to the session TTL.
            error_code = "vendor_forbidden" if exc.status == 403 else "vendor_error"
            return StatusReport(
                kind="failed",
                error_code=error_code,
                terminal_detail=f"vendor {exc.status}",
            )

    async def _poll_vendor(
        self,
        credential_id: str,
        dfc: DeviceAuthorizationCredential | None,
    ) -> StatusReport:
        assert dfc is not None
        assert dfc.encrypted_device_code is not None

        device_code = self._ctx.encryption.decrypt(dfc.encrypted_device_code)
        result = await device_authorization.poll_device_authorization(
            token_endpoint=dfc.token_url,
            client_id=dfc.client_id,
            device_code=device_code,
        )

        now = datetime.now(UTC)
        async with self._ctx.control_db.transaction() as session:
            await DeviceAuthorizationCredentialRepository.mark_polled(session, credential_id, now)

        if result.status == "pending":
            return StatusReport(kind="pending")
        if result.status == "slow_down":
            # RFC 8628 §3.5 — widen the interval by 5s for future polls.
            async with self._ctx.control_db.transaction() as session:
                await DeviceAuthorizationCredentialRepository.update_fields(
                    session,
                    credential_id,
                    poll_interval_seconds=(dfc.poll_interval_seconds or 5) + 5,
                )
            return StatusReport(kind="pending")
        if result.status == "denied":
            return StatusReport(
                kind="failed",
                error_code="access_denied",
                terminal_detail="access_denied",
            )
        if result.status == "expired":
            return StatusReport(
                kind="expired",
                error_code="expired_token",
                terminal_detail="expired_token",
            )

        # success
        assert result.access_token is not None
        return StatusReport(
            kind="success",
            tokens=SuccessTokens(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                expires_in=result.expires_in,
                scope=result.scope,
                # The vendor's ``scope`` field is unreliable for device
                # flow (GitHub, for one, returns empty). Use the
                # user-confirmed list as the authoritative "what was
                # granted" record — service persists it verbatim onto
                # ``oauth_token.scope`` for terminal readback.
                granted_scopes=dfc.granted_scopes,
            ),
        )

    async def on_finalise(
        self,
        db_session: AsyncSession,
        *,
        credential_id: str,
    ) -> None:
        # Clear encrypted_device_code + user_code once the token is vaulted:
        # transient artefacts have no operational value after ``connected``
        # and shouldn't hang around encrypted.
        await DeviceAuthorizationCredentialRepository.clear_transient(db_session, credential_id)


def _should_poll_now(dfc: DeviceAuthorizationCredential | None) -> bool:
    """Rate-limit the vendor poll to at most once per ``poll_interval_seconds``."""
    if dfc is None or dfc.poll_interval_seconds is None:
        return True
    if dfc.last_polled_at is None:
        return True
    elapsed = (datetime.now(UTC) - dfc.last_polled_at).total_seconds()
    return elapsed >= dfc.poll_interval_seconds
