"""Device-flow (RFC 8628) handler for the connect-session state machine.

Owns the ``device_authorization_credentials`` aux table, the RFC 8628 device-auth
request at confirm time, and the poll loop against the vendor's token
endpoint. Bound-scope readback lives on the shared ``oauth_token.scope``
column, written at finalise time — the service reads it flow-agnostically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.services.integrations import device_authorization
from jentic_one.control.services.integrations.flow_handlers.base import (
    BeginResult,
    DeviceAuthorizationBeginResult,
    StatusReport,
    SuccessTokens,
)
from jentic_one.control.services.integrations.flow_handlers.session_app import SessionApp
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import StoredCredentialType

# Upper bound on the RFC 8628 §3.5 ``slow_down`` interval widening. Every
# ``slow_down`` bumps the interval by 5s; without a cap a jittery vendor could
# compound this until the interval outlived the connect session TTL. 60s is
# well above any real vendor's happy-path interval (GitHub / Google ship 5s)
# and below the default session TTL, so a legit slow_down cycle still lets
# the scanner drive the flow to completion.
_MAX_POLL_INTERVAL_SECONDS = 60


class DeviceAuthorizationHandler:
    """RFC 8628 device-flow handler."""

    kind: ClassVar[str] = "device_authorization"
    stored_type: ClassVar[StoredCredentialType] = StoredCredentialType.OAUTH2_DEVICE_CODE
    provider_id: ClassVar[str] = "device_authorization"

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
        # Device flow (RFC 8628) needs the aux row unconditionally: it carries
        # the transient device_code / poll bookkeeping that has no home on the
        # credential row. Its ``client_id`` / ``token_url`` /
        # ``authorization_endpoint`` columns are non-null, so we populate them
        # from either source (the ``advance`` path prefers the registration
        # when the credential's FK is set — see ``_load_endpoints_for_credential``).
        # ``requested_scopes`` lives on the connect_sessions row now
        # (flow-agnostic); pass an empty list to the aux row so the column
        # sees no stale copy. Left non-NULL to preserve the schema shape.
        assert app.authorization_endpoint is not None and app.token_endpoint is not None, (
            "device_authorization SessionApp requires authorization_endpoint + token_endpoint"
        )
        await DeviceAuthorizationCredentialRepository.create(
            db_session,
            credential_id=credential_id,
            client_id=app.client_id,
            token_url=app.token_endpoint,
            authorization_endpoint=app.authorization_endpoint,
            requested_scopes=[],
            created_by=created_by,
        )
        if app.registration_id is not None:
            # Shared-registration path: FK + owner stamp so the broker's
            # binding resolver can enforce owner scope, and the refresh /
            # advance paths can dereference the registration for current
            # endpoints even if the aux row has drifted.
            await CredentialRepository.set_oauth_app_registration(
                db_session,
                credential_id,
                registration_id=app.registration_id,
                owner_user_id=owner_user_id,
            )

    async def begin(
        self,
        row: ConnectSession,
        *,
        app: SessionApp,
        confirmed_scopes: list[str],
    ) -> BeginResult:
        assert app.authorization_endpoint is not None, (
            "device_authorization SessionApp requires authorization_endpoint"
        )

        # Talk to the vendor OUTSIDE the DB transaction — network latency
        # has no business holding a control-DB row lock.
        result = await device_authorization.begin_device_authorization(
            authorization_endpoint=app.authorization_endpoint,
            client_id=app.client_id,
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

        # RFC 8628 poll-interval rate limit — atomic claim so two scanner
        # replicas cannot both pass the "interval elapsed" guard on the
        # same credential and double-poll the vendor. The old
        # ``_should_poll_now`` + late ``mark_polled`` was a TOCTOU: read
        # here, mark after the vendor call. The DB-side compare-and-swap
        # in ``try_claim_poll_slot`` closes it. A row with no
        # ``poll_interval_seconds`` yet (transient between ``begin`` and
        # ``set_transient_state``) is refused the claim and reports
        # pending — the next scanner tick will catch it.
        if dfc is None or dfc.poll_interval_seconds is None:
            return StatusReport(kind="pending")
        now = datetime.now(UTC)
        min_last_polled_at = now - timedelta(seconds=dfc.poll_interval_seconds)
        async with self._ctx.control_db.transaction() as claim_session:
            claimed = await DeviceAuthorizationCredentialRepository.try_claim_poll_slot(
                claim_session,
                credential_id,
                now=now,
                min_last_polled_at=min_last_polled_at,
            )
        if not claimed:
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
        client_id, token_endpoint = await self._resolve_poll_endpoints(credential_id, dfc)
        result = await device_authorization.poll_device_authorization(
            token_endpoint=token_endpoint,
            client_id=client_id,
            device_code=device_code,
        )

        # ``last_polled_at`` is stamped up in ``advance`` by
        # ``try_claim_poll_slot`` before the vendor call — the atomic
        # claim is what serialises concurrent scanner replicas. Stamping
        # again here would just clobber it with the same value; skip.

        if result.status == "pending":
            return StatusReport(kind="pending")
        if result.status == "slow_down":
            # RFC 8628 §3.5 — widen the interval by 5s for future polls.
            # Cap so a jittery vendor that fires repeated ``slow_down``
            # errors doesn't compound the interval to the session TTL.
            widened = min((dfc.poll_interval_seconds or 5) + 5, _MAX_POLL_INTERVAL_SECONDS)
            async with self._ctx.control_db.transaction() as session:
                await DeviceAuthorizationCredentialRepository.update_fields(
                    session,
                    credential_id,
                    poll_interval_seconds=widened,
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
        if result.status != "success":
            # Exhaustive: a new/unknown status from ``poll_device_authorization``
            # must surface as a typed terminal failure, not fall into the
            # success branch and blow up on the ``access_token`` assert.
            return StatusReport(
                kind="failed",
                error_code="vendor_error",
                terminal_detail=f"unknown poll status {result.status!r}",
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
        db_session: Any,
        *,
        credential_id: str,
    ) -> None:
        # Clear encrypted_device_code + user_code once the token is vaulted:
        # transient artefacts have no operational value after ``connected``
        # and shouldn't hang around encrypted.
        await DeviceAuthorizationCredentialRepository.clear_transient(db_session, credential_id)

    async def _resolve_poll_endpoints(
        self,
        credential_id: str,
        dfc: DeviceAuthorizationCredential,
    ) -> tuple[str, str]:
        """Return the ``(client_id, token_endpoint)`` used for one poll tick.

        Dereferences through ``oauth_app_registrations`` when the credential
        was minted through a shared registration (``credentials.oauth_app_registration_id``
        set) — that keeps rotated endpoints or a rotated client_id in effect
        for in-flight sessions even if the aux row's copy has drifted. Falls
        back to the aux row for legacy embedded credentials.
        """
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None or credential.oauth_app_registration_id is None:
                return dfc.client_id, dfc.token_url
            registration = await OAuthAppRegistrationRepository.get_by_id(
                session, credential.oauth_app_registration_id
            )
            if registration is None or registration.device_authorization_details is None:
                return dfc.client_id, dfc.token_url
            return (
                registration.client_id,
                registration.device_authorization_details.token_endpoint,
            )
