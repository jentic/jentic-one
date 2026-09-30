"""Token refresh service — lazy single-flight refresh for OAuth2 credentials."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from jentic_one.broker.services.credentials.errors import (
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.providers.base import (
    ProviderError,
    UnknownProviderError,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import InvalidGrantError
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.shared.context import Context
from jentic_one.shared.redaction import redact_value

logger = structlog.get_logger(__name__)

_sqlite_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

_DEFAULT_EXPIRY_SKEW_SECONDS = 60


class TokenRefresher:
    """Ensures an OAuth2 access token is fresh before injection.

    Implements single-flight refresh: per-credential advisory lock (Postgres)
    or asyncio.Lock (SQLite), with double-check after acquiring.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def ensure_fresh(self, *, resolved: ResolvedCredential, caller: str) -> str:
        """Return a valid cleartext access token, refreshing if necessary."""
        skew = self._get_expiry_skew(resolved.provider)

        if self._is_valid(resolved, skew):
            return self._ctx.encryption.decrypt(resolved.encrypted_access_token)  # type: ignore[arg-type]

        return await self._refresh_with_lock(resolved, skew, caller)

    def _get_expiry_skew(self, provider_name: str) -> timedelta:
        try:
            provider = self._ctx.providers.get(provider_name)
            skew_seconds = getattr(provider, "_expiry_skew_seconds", _DEFAULT_EXPIRY_SKEW_SECONDS)
        except UnknownProviderError:
            skew_seconds = _DEFAULT_EXPIRY_SKEW_SECONDS
        return timedelta(seconds=skew_seconds)

    @staticmethod
    def _is_valid(resolved: ResolvedCredential, skew: timedelta) -> bool:
        if resolved.encrypted_access_token is None:
            return False
        if resolved.token_expires_at is None:
            return True
        return resolved.token_expires_at - skew > datetime.now(UTC)

    async def _refresh_with_lock(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        dialect = self._ctx.control_db.backend.dialect_name
        if dialect == "sqlite":
            return await self._refresh_sqlite(resolved, skew, caller)
        return await self._refresh_postgres(resolved, skew, caller)

    async def _refresh_sqlite(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        lock = _sqlite_locks[resolved.credential_id]
        async with lock:
            return await self._double_check_and_refresh(resolved, skew, caller)

    async def _refresh_postgres(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        async with self._ctx.control_db.transaction() as session:
            await OAuthTokenRepository.acquire_refresh_lock(session, resolved.credential_id)

            row = await OAuthTokenRepository.get_by_credential(session, resolved.credential_id)
            if (
                row is not None
                and row.encrypted_access_token
                and (row.expires_at is None or row.expires_at - skew > datetime.now(UTC))
            ):
                return self._ctx.encryption.decrypt(row.encrypted_access_token)

            fresh_token = await self._do_refresh(resolved, caller)

            encrypted_access = self._ctx.encryption.encrypt(fresh_token.access_token)
            encrypted_refresh = (
                self._ctx.encryption.encrypt(fresh_token.refresh_token)
                if fresh_token.refresh_token
                else None
            )
            updated = await OAuthTokenRepository.update_tokens(
                session,
                resolved.credential_id,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=fresh_token.expires_at,
                scope=fresh_token.scope,
            )
            if updated is None:
                logger.error(
                    "refresh_token_row_missing",
                    credential_id=resolved.credential_id,
                )

            return fresh_token.access_token

    async def _double_check_and_refresh(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        async with self._ctx.control_db.transaction() as session:
            row = await OAuthTokenRepository.get_by_credential(session, resolved.credential_id)
            if (
                row is not None
                and row.encrypted_access_token
                and (row.expires_at is None or row.expires_at - skew > datetime.now(UTC))
            ):
                return self._ctx.encryption.decrypt(row.encrypted_access_token)

            fresh_token = await self._do_refresh(resolved, caller)

            encrypted_access = self._ctx.encryption.encrypt(fresh_token.access_token)
            encrypted_refresh = (
                self._ctx.encryption.encrypt(fresh_token.refresh_token)
                if fresh_token.refresh_token
                else None
            )
            updated = await OAuthTokenRepository.update_tokens(
                session,
                resolved.credential_id,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=fresh_token.expires_at,
                scope=fresh_token.scope,
            )
            if updated is None:
                logger.error(
                    "refresh_token_row_missing",
                    credential_id=resolved.credential_id,
                )

            return fresh_token.access_token

    async def _do_refresh(self, resolved: ResolvedCredential, caller: str) -> _RefreshOutcome:
        try:
            provider = self._ctx.providers.get(resolved.provider)
        except UnknownProviderError as exc:
            raise RefreshTransientError(
                resolved.credential_id, f"unknown provider: {resolved.provider}"
            ) from exc

        async def _decrypt_refresh_token() -> str:
            if resolved.encrypted_refresh_token is None:
                raise ProviderError("No refresh token available")
            return self._ctx.encryption.decrypt(resolved.encrypted_refresh_token)

        token_view = OAuthTokenView(
            credential_id=resolved.credential_id,
            provider=resolved.provider,
            provider_account_ref=resolved.provider_account_ref,
            expires_at=resolved.token_expires_at,
            decrypt=_decrypt_refresh_token,
        )

        try:
            result = await provider.refresh(self._ctx, token=token_view)
        except InvalidGrantError as exc:
            logger.warning(
                "refresh_invalid_grant",
                credential_id=resolved.credential_id,
                provider=resolved.provider,
            )
            raise RefreshInvalidGrantError(resolved.credential_id) from exc
        except (httpx.TimeoutException, httpx.ConnectError, ProviderError) as exc:
            # The exception text (httpx transport message, IdP error body) can
            # quote request material, so only its class reaches the caller; the
            # server-side log keeps a redacted copy for diagnosis. ``from None``
            # keeps the raw message out of any traceback rendered downstream.
            logger.warning(
                "refresh_transient_error",
                credential_id=resolved.credential_id,
                provider=resolved.provider,
                error_type=type(exc).__name__,
                error=redact_value(str(exc)),
            )
            raise RefreshTransientError(resolved.credential_id, type(exc).__name__) from None

        return _RefreshOutcome(
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            expires_at=result.expires_at,
            scope=result.scope,
        )


class _RefreshOutcome:
    """Internal value object for a successful refresh result."""

    __slots__ = ("access_token", "expires_at", "refresh_token", "scope")

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scope: str | None,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.scope = scope
