"""Vendor CLI invocation tokens: mint, renew and revoke.

``jentic gh``/``cf``/``aws`` mint one ``cit_`` token per command, renew it on a
heartbeat while the command runs and revoke it on exit. The token is only
accepted by the broker's CLI facades, and every resolution there re-resolves
the parent ``at_`` access token it was minted with.

Not audited. A mint carries no authority of its own (the parent token's
issuance is the audited event, and the child dies with it), and the wrapper
mints once per command, so audit rows would be per-command noise. Each call
the token makes is recorded as an execution under the token's trace id, and
mint/renew/revoke are logged as structured events.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.cli_invocations import CliInvocation
from jentic_one.admin.repos import AccessTokenRepository, CliInvocationRepository
from jentic_one.auth.services.errors import (
    CliFacadeUnavailableError,
    CliInvocationMintForbiddenError,
    CliInvocationNotFoundError,
    CliInvocationNotRenewableError,
)
from jentic_one.auth.services.schemas.cli_invocations import (
    CliInvocationMintPayload,
    CliInvocationView,
    MintedCliInvocation,
)
from jentic_one.auth.services.token_service import ACCESS_TOKEN_PREFIX
from jentic_one.shared.auth.cli_invocation import CLI_INVOCATION_TOKEN_PREFIX, hash_token
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)


def _new_trace_id() -> str:
    """A random W3C trace id (32 lowercase hex chars, never all zeros)."""
    while True:
        trace_id = secrets.token_hex(16)
        if trace_id.strip("0"):
            return trace_id


def _view(row: CliInvocation) -> CliInvocationView:
    return CliInvocationView(
        id=row.id,
        vendor=row.vendor,
        credential_name=row.credential_name,
        trace_id=row.trace_id,
        command_title=row.command_title,
        expires_at=row.expires_at,
        hard_expires_at=row.hard_expires_at,
        revoked_at=row.revoked_at,
    )


class CliInvocationService:
    """Issues and manages ``cit_`` invocation tokens for an agent's own commands."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._cfg = ctx.config.cli_facades

    def require_vendor(self, vendor: str) -> None:
        """Raise unless ``vendor`` has a served facade (both switches on)."""
        if not self._cfg.vendor_enabled(vendor):
            raise CliFacadeUnavailableError(vendor)

    async def mint(
        self,
        payload: CliInvocationMintPayload,
        *,
        identity: Identity,
        parent_token: str,
    ) -> MintedCliInvocation:
        """Mint an invocation token for the calling agent.

        ``parent_token`` is the credential the caller authenticated with; it
        must be the agent's own live ``at_`` access token. The token's lifetime
        is capped by the parent's expiry, since the child stops resolving once
        the parent does. ``payload.credential_name`` is stored as given, not
        checked here: the broker applies it on each call through the same
        selection as the ``Jentic-Credential-Name`` header, against the agent's
        live bindings, so an unknown name fails that call with the usual
        problem.
        """
        self.require_vendor(payload.vendor)
        if identity.actor_type != ActorType.AGENT or not parent_token.startswith(
            ACCESS_TOKEN_PREFIX
        ):
            raise CliInvocationMintForbiddenError()

        parent_hash = hash_token(parent_token)
        token = f"{CLI_INVOCATION_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        token_hash = hash_token(token)
        trace_id = _new_trace_id()
        now = datetime.now(UTC)

        async def _write(session: AsyncSession) -> CliInvocation:
            parent = await AccessTokenRepository.get_by_hash(session, parent_hash)
            if (
                parent is None
                or parent.actor_id != identity.sub
                or parent.actor_type != ActorType.AGENT.value
                or parent.revoked_at is not None
                or parent.expires_at <= now
            ):
                raise CliInvocationMintForbiddenError()
            hard_expires_at = min(
                now + timedelta(seconds=self._cfg.invocation_hard_cap_seconds), parent.expires_at
            )
            expires_at = min(
                now + timedelta(seconds=self._cfg.invocation_ttl_seconds), hard_expires_at
            )
            return await CliInvocationRepository.create(
                session,
                token_hash=token_hash,
                parent_token_hash=parent_hash,
                actor_id=identity.sub,
                actor_type=ActorType.AGENT.value,
                vendor=payload.vendor,
                credential_name=payload.credential_name,
                trace_id=trace_id,
                command_title=payload.command_title,
                expires_at=expires_at,
                hard_expires_at=hard_expires_at,
            )

        # Token generation stays outside the retried write so the returned
        # secret is stable across a retry (the TokenService pattern).
        row = await self._ctx.admin_db.run_in_transaction(_write)
        logger.info(
            "cli_invocation_minted",
            invocation_id=row.id,
            actor_id=identity.sub,
            vendor=row.vendor,
            trace_id=row.trace_id,
            expires_at=row.expires_at.isoformat(),
            hard_expires_at=row.hard_expires_at.isoformat(),
        )
        return MintedCliInvocation(invocation=_view(row), token=token)

    async def renew(self, invocation_id: str, *, identity: Identity) -> CliInvocationView:
        """Extend the caller's invocation by the TTL, never past its hard cap."""
        now = datetime.now(UTC)
        async with self._ctx.admin_db.transaction() as session:
            row = await CliInvocationRepository.get_for_actor_for_update(
                session, invocation_id, actor_id=identity.sub
            )
            if row is None:
                raise CliInvocationNotFoundError(invocation_id)
            if row.revoked_at is not None:
                raise CliInvocationNotRenewableError(invocation_id, "it is revoked")
            if row.expires_at <= now or row.hard_expires_at <= now:
                raise CliInvocationNotRenewableError(invocation_id, "it has expired")
            expires_at = min(
                now + timedelta(seconds=self._cfg.invocation_ttl_seconds), row.hard_expires_at
            )
            row = await CliInvocationRepository.set_expires_at(session, row, expires_at)
            view = _view(row)
        logger.info(
            "cli_invocation_renewed",
            invocation_id=invocation_id,
            actor_id=identity.sub,
            expires_at=view.expires_at.isoformat(),
        )
        return view

    async def revoke(self, invocation_id: str, *, identity: Identity) -> None:
        """Revoke the caller's invocation. Revoking a revoked one is a no-op."""
        async with self._ctx.admin_db.transaction() as session:
            row = await CliInvocationRepository.get_for_actor_for_update(
                session, invocation_id, actor_id=identity.sub
            )
            if row is None:
                raise CliInvocationNotFoundError(invocation_id)
            if row.revoked_at is not None:
                return
            await CliInvocationRepository.revoke(session, row, datetime.now(UTC))
        logger.info("cli_invocation_revoked", invocation_id=invocation_id, actor_id=identity.sub)
