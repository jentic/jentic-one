"""Token refresh service — lazy single-flight refresh for OAuth2 credentials.

Expiry semantics: ``oauth_tokens.expires_at`` (and so
``ResolvedCredential.token_expires_at``) is the access token's true expiry as
the vendor reported it (``issued_at + expires_in``) — providers never
subtract a safety margin when they compute it. The provider's
``expiry_skew_seconds`` is applied exactly once, here, at comparison time: a
token is fresh while ``expires_at - skew > now``. A stored expiry that
already has the skew subtracted still compares safely — it refreshes up to
one skew earlier than needed, and that refresh rewrites it with the true
expiry.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from jentic_one.broker.services.credentials.errors import (
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.providers.base import (
    ProviderError,
    UnknownProviderError,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import InvalidGrantError
from jentic_one.control.services.credentials.providers.oauth2 import (
    parse_oauth_expires_in,
    true_oauth_expiry,
)
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import DecryptionError
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.db.locks import credential_refresh_lock_key
from jentic_one.shared.redaction import redact_value

_true_expiry = true_oauth_expiry

__all__ = [
    "TokenRefresher",
    "parse_oauth_expires_in",
    "true_oauth_expiry",
]

logger = structlog.get_logger(__name__)

_DEFAULT_EXPIRY_SKEW_SECONDS = 60
_LOCK_WAIT_TIMEOUT_S = 35.0
_LOCK_POLL_BASE_S = 0.02
_LOCK_POLL_MAX_S = 0.1
_COMMIT_MAX_ATTEMPTS = 3
_COMMIT_RETRY_BACKOFF_S = 0.05
_SHIELD_DRAIN_TIMEOUT_S = 35.0


def _is_fresh(expires_at: datetime | None, skew: timedelta, now: datetime) -> bool:
    """True when a token with true expiry ``expires_at`` is usable beyond ``skew``.

    The single place the skew is applied — see the module docstring.
    """
    return expires_at is None or expires_at - skew > now


async def _run_uninterruptible[T](
    coro: Coroutine[Any, Any, T], *, drain_timeout_s: float = _SHIELD_DRAIN_TIMEOUT_S
) -> T:
    """Await ``coro`` to completion even when the caller task is cancelled.

    Once a refresh contacts a rotating OAuth vendor, aborting mid-flight or
    before committing the response loses the newly issued refresh token while
    leaving the invalidated refresh token in storage. When the caller is
    cancelled, this helper drains ``coro`` up to ``drain_timeout_s`` so the
    rotated grant commits durably, retrieves any task exception so asyncio
    leaves no unretrieved error, and then re-raises ``CancelledError``.
    """
    task = asyncio.create_task(coro)
    cancelled = False
    cancel_deadline: float | None = None
    loop = asyncio.get_running_loop()
    while not task.done():
        try:
            if cancel_deadline is None:
                await asyncio.shield(task)
            else:
                remaining = cancel_deadline - loop.time()
                if remaining <= 0:
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
                    break
                async with asyncio.timeout(remaining):
                    await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            break
        except asyncio.CancelledError:
            cancelled = True
            if cancel_deadline is None:
                cancel_deadline = loop.time() + drain_timeout_s
    if cancelled:
        if task.done() and not task.cancelled():
            _ = task.exception()
        raise asyncio.CancelledError
    return task.result()


class TokenRefresher:
    """Ensures an OAuth2 access token is fresh before injection.

    Implements two-tier single-flight refresh:
    1. Per-``DatabaseSession`` refcounted ``asyncio.Lock`` per credential so
       concurrent callers in the same process coalesce without touching the DB
       connection pool.
    2. On PostgreSQL, a non-blocking session-level advisory lock multiplexed
       onto a dedicated ``AUTOCOMMIT`` lock connection, paired with short
       read/write sessions so no pooled DB connection or transaction is held
       across upstream HTTP calls or cross-process lock waits.
    """

    _lock_wait_timeout_s: float = _LOCK_WAIT_TIMEOUT_S
    _shield_drain_timeout_s: float = _SHIELD_DRAIN_TIMEOUT_S

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
        return _is_fresh(resolved.token_expires_at, skew, datetime.now(UTC))

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
        async with self._ctx.control_db.keyed_lock(resolved.credential_id):
            return await _run_uninterruptible(
                self._refresh_sqlite_inner(resolved, skew, caller),
                drain_timeout_s=self._shield_drain_timeout_s,
            )

    async def _refresh_sqlite_inner(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        fresh_token: _RefreshOutcome | None = None
        prepared: _PreparedRefresh | None = None
        encrypted_access: str | None = None
        encrypted_refresh: str | None = None
        try:
            async with self._ctx.control_db.transaction() as session:
                row = await OAuthTokenRepository.get_by_credential(session, resolved.credential_id)
                outcome = self._prepare_refresh_from_row(resolved, row, skew)
                if isinstance(outcome, str):
                    return outcome
                prepared = outcome
                fresh_token = await self._do_refresh(prepared.resolved, caller)
                if prepared.mints_without_row:
                    return fresh_token.access_token
                encrypted_access = self._ctx.encryption.encrypt(fresh_token.access_token)
                encrypted_refresh = (
                    self._ctx.encryption.encrypt(fresh_token.refresh_token)
                    if fresh_token.refresh_token
                    else None
                )
                return await self._write_refreshed_row(
                    session,
                    credential_id=prepared.resolved.credential_id,
                    encrypted_access=encrypted_access,
                    encrypted_refresh=encrypted_refresh,
                    fresh_token=fresh_token,
                    expected_access_token=prepared.expected_access_token,
                )
        except DatabaseUnavailableError:
            if (
                fresh_token is None
                or prepared is None
                or prepared.mints_without_row
                or encrypted_access is None
            ):
                raise
            return await self._persist_with_retries(
                prepared=prepared,
                fresh_token=fresh_token,
                encrypted_access=encrypted_access,
                encrypted_refresh=encrypted_refresh,
            )

    async def _refresh_postgres(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        lock_key = credential_refresh_lock_key(resolved.credential_id)
        loop = asyncio.get_running_loop()
        async with self._ctx.control_db.keyed_lock(resolved.credential_id) as waited:
            if waited:
                reused = await self._recheck_reusable_token(resolved, skew)
                if reused is not None:
                    return reused

            deadline = loop.time() + self._lock_wait_timeout_s
            attempt = 0
            while True:
                async with self._ctx.control_db.try_shared_advisory_lock(lock_key) as acquired:
                    if acquired:
                        return await self._refresh_under_postgres_lock(resolved, skew, caller)

                reused = await self._recheck_reusable_token(resolved, skew)
                if reused is not None:
                    return reused

                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning(
                        "refresh_lock_timeout",
                        credential_id=resolved.credential_id,
                        provider=resolved.provider,
                    )
                    raise RefreshTransientError(resolved.credential_id, "RefreshLockTimeout")
                delay = min(
                    _LOCK_POLL_BASE_S * (1.5 ** min(attempt, 6)),
                    _LOCK_POLL_MAX_S,
                    remaining,
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def _recheck_reusable_token(
        self, resolved: ResolvedCredential, skew: timedelta
    ) -> str | None:
        """Return a decrypted live token if another holder has already refreshed."""
        async with self._ctx.control_db.session() as session:
            row = await OAuthTokenRepository.get_by_credential(session, resolved.credential_id)
        if row is None or row.revoked_at is not None or not row.encrypted_access_token:
            return None
        now = datetime.now(UTC)
        if _is_fresh(row.expires_at, skew, now):
            return self._ctx.encryption.decrypt(row.encrypted_access_token)
        if row.encrypted_access_token != resolved.encrypted_access_token and (
            row.expires_at is None or row.expires_at > now
        ):
            return self._ctx.encryption.decrypt(row.encrypted_access_token)
        return None

    async def _refresh_under_postgres_lock(
        self, resolved: ResolvedCredential, skew: timedelta, caller: str
    ) -> str:
        """Inspect the locked row in a short session, then exchange and commit."""
        async with self._ctx.control_db.session() as session:
            row = await OAuthTokenRepository.get_by_credential(session, resolved.credential_id)
        outcome = self._prepare_refresh_from_row(resolved, row, skew)
        if isinstance(outcome, str):
            return outcome
        return await _run_uninterruptible(
            self._exchange_and_persist_postgres(outcome, caller),
            drain_timeout_s=self._shield_drain_timeout_s,
        )

    async def _exchange_and_persist_postgres(self, prepared: _PreparedRefresh, caller: str) -> str:
        fresh_token = await self._do_refresh(prepared.resolved, caller)
        if prepared.mints_without_row:
            return fresh_token.access_token
        # Encrypt once before the commit retry loop so an idempotent retry after
        # an ambiguous commit drop recognises its own ciphertext if the first
        # attempt actually committed on the server.
        encrypted_access = self._ctx.encryption.encrypt(fresh_token.access_token)
        encrypted_refresh = (
            self._ctx.encryption.encrypt(fresh_token.refresh_token)
            if fresh_token.refresh_token
            else None
        )
        return await self._persist_with_retries(
            prepared=prepared,
            fresh_token=fresh_token,
            encrypted_access=encrypted_access,
            encrypted_refresh=encrypted_refresh,
        )

    async def _persist_with_retries(
        self,
        *,
        prepared: _PreparedRefresh,
        fresh_token: _RefreshOutcome,
        encrypted_access: str,
        encrypted_refresh: str | None,
    ) -> str:
        for attempt in range(_COMMIT_MAX_ATTEMPTS):
            try:
                async with self._ctx.control_db.transaction() as session:
                    return await self._write_refreshed_row(
                        session,
                        credential_id=prepared.resolved.credential_id,
                        encrypted_access=encrypted_access,
                        encrypted_refresh=encrypted_refresh,
                        fresh_token=fresh_token,
                        expected_access_token=prepared.expected_access_token,
                    )
            except DatabaseUnavailableError as exc:
                if attempt + 1 >= _COMMIT_MAX_ATTEMPTS:
                    logger.error(
                        "refresh_persist_failed",
                        credential_id=prepared.resolved.credential_id,
                        provider=prepared.resolved.provider,
                        attempts=attempt + 1,
                        error_type=type(exc).__name__,
                    )
                    raise
                logger.warning(
                    "refresh_persist_retrying",
                    credential_id=prepared.resolved.credential_id,
                    provider=prepared.resolved.provider,
                    attempt=attempt + 1,
                    max_attempts=_COMMIT_MAX_ATTEMPTS,
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(_COMMIT_RETRY_BACKOFF_S * (attempt + 1))
        raise AssertionError("unreachable")

    async def _write_refreshed_row(
        self,
        session: Any,
        *,
        credential_id: str,
        encrypted_access: str,
        encrypted_refresh: str | None,
        fresh_token: _RefreshOutcome,
        expected_access_token: str | None,
    ) -> str:
        row = await OAuthTokenRepository.get_by_credential(session, credential_id, for_update=True)
        if row is None:
            logger.error("refresh_token_row_missing", credential_id=credential_id)
            raise RefreshInvalidGrantError(credential_id)
        if row.revoked_at is not None:
            logger.warning(
                "refresh_grant_missing",
                credential_id=credential_id,
                revoked=True,
            )
            raise RefreshInvalidGrantError(credential_id)
        if row.encrypted_access_token == encrypted_access:
            return fresh_token.access_token
        if (
            expected_access_token is not None
            and row.encrypted_access_token != expected_access_token
        ):
            # Another writer (such as a reconnect after a lost lock connection)
            # replaced the grant while the token exchange was in flight; keep
            # the newer stored grant intact rather than overwriting it.
            now = datetime.now(UTC)
            if row.encrypted_access_token and (row.expires_at is None or row.expires_at > now):
                return self._ctx.encryption.decrypt(row.encrypted_access_token)
            raise RefreshInvalidGrantError(credential_id)

        updated = await OAuthTokenRepository.update_tokens(
            session,
            credential_id,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            expires_at=fresh_token.expires_at,
            scope=fresh_token.scope,
        )
        if updated is None:
            logger.error("refresh_token_row_missing", credential_id=credential_id)
            raise RefreshInvalidGrantError(credential_id)
        return fresh_token.access_token

    def _prepare_refresh_from_row(
        self, resolved: ResolvedCredential, row: OAuthToken | None, skew: timedelta
    ) -> str | _PreparedRefresh:
        """Validate the post-lock token row and return either a live token or refresh inputs.

        ``resolved`` is a snapshot read before the lock, so another holder may
        have refreshed — and rotated the refresh token — in the meantime. The
        row re-read under the lock is authoritative for both the freshness
        check and the refresh token sent to the vendor: replaying a
        rotated-out refresh token gets ``invalid_grant``, and providers that
        detect refresh-token reuse revoke the whole grant.

        A missing or revoked row under the lock means the grant is gone (a
        disconnect, or a ``token_url`` move to another origin, which takes this
        same lock to drop the row), so the credential must be reconnected. The
        snapshot's refresh token is never used in that case: it belongs to the
        old grant and may be bound to a different token endpoint. Only a
        provider that connects as an account reference
        (``ProviderRegistry.mints_from_account_ref``) mints with no row, from
        ``provider_account_ref``; on any other provider that column is just a
        display name and does not stand in for a grant.
        """
        has_account_ref = bool(resolved.provider_account_ref) and (
            self._ctx.providers.mints_from_account_ref(resolved.provider)
        )
        if row is None or row.revoked_at is not None:
            if not (row is None and has_account_ref):
                logger.warning(
                    "refresh_grant_missing",
                    credential_id=resolved.credential_id,
                    revoked=row is not None,
                )
                raise RefreshInvalidGrantError(resolved.credential_id)
            return _PreparedRefresh(
                resolved=resolved.model_copy(
                    update={
                        "encrypted_access_token": None,
                        "encrypted_refresh_token": None,
                        "token_expires_at": None,
                    }
                ),
                mints_without_row=True,
                expected_access_token=None,
            )

        now = datetime.now(UTC)
        if row.encrypted_access_token and _is_fresh(row.expires_at, skew, now):
            return self._ctx.encryption.decrypt(row.encrypted_access_token)
        # Every encryption draws a fresh nonce, so a ciphertext that differs
        # from the snapshot's means the row was rewritten after the snapshot
        # (another holder refreshed, or a reconnect landed). That token is the
        # newest the vendor issued; use it until it actually expires rather
        # than refreshing again inside the skew window, which would burn
        # another rotation (and loop for tokens whose lifetime is shorter than
        # the skew).
        if (
            row.encrypted_access_token
            and row.encrypted_access_token != resolved.encrypted_access_token
            and (row.expires_at is None or row.expires_at > now)
        ):
            return self._ctx.encryption.decrypt(row.encrypted_access_token)
        if not has_account_ref and (
            row.encrypted_refresh_token is None or not row.encrypted_refresh_token.strip()
        ):
            logger.warning(
                "refresh_grant_missing",
                credential_id=resolved.credential_id,
                revoked=False,
            )
            raise RefreshInvalidGrantError(resolved.credential_id)

        return _PreparedRefresh(
            resolved=resolved.model_copy(
                update={
                    "encrypted_access_token": row.encrypted_access_token,
                    "encrypted_refresh_token": row.encrypted_refresh_token,
                    "token_expires_at": row.expires_at,
                }
            ),
            mints_without_row=False,
            expected_access_token=row.encrypted_access_token,
        )

    async def _do_refresh(self, resolved: ResolvedCredential, caller: str) -> _RefreshOutcome:
        try:
            provider = self._ctx.providers.get(resolved.provider)
        except UnknownProviderError as exc:
            raise RefreshTransientError(
                resolved.credential_id, f"unknown provider: {resolved.provider}"
            ) from exc

        async def _decrypt_refresh_token() -> str:
            if (
                resolved.encrypted_refresh_token is None
                or not resolved.encrypted_refresh_token.strip()
            ):
                raise InvalidGrantError("No refresh token available")
            try:
                plaintext = self._ctx.encryption.decrypt(resolved.encrypted_refresh_token)
            except DecryptionError as exc:
                raise InvalidGrantError("No refresh token available") from exc
            if not plaintext or not plaintext.strip():
                raise InvalidGrantError("No refresh token available")
            return plaintext

        token_view = OAuthTokenView(
            credential_id=resolved.credential_id,
            provider=resolved.provider,
            provider_account_ref=resolved.provider_account_ref,
            expires_at=resolved.token_expires_at,
            decrypt=_decrypt_refresh_token,
        )

        try:
            result = await provider.refresh(self._ctx, token=token_view)
            if not isinstance(result.access_token, str) or not result.access_token.strip():
                raise ProviderError("Provider refresh returned no access_token")
        except InvalidGrantError as exc:
            logger.warning(
                "refresh_invalid_grant",
                credential_id=resolved.credential_id,
                provider=resolved.provider,
            )
            raise RefreshInvalidGrantError(resolved.credential_id) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ProviderError) as exc:
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


class _PreparedRefresh:
    """Authoritative inputs read from the locked token row prior to exchange."""

    __slots__ = ("expected_access_token", "mints_without_row", "resolved")

    def __init__(
        self,
        *,
        resolved: ResolvedCredential,
        mints_without_row: bool,
        expected_access_token: str | None,
    ) -> None:
        self.resolved = resolved
        self.mints_without_row = mints_without_row
        self.expected_access_token = expected_access_token


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
