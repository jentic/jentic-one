"""Unit tests for OAuthRevocationService (RFC 7009 public-client revocation, G11).

Repo-mocked (the SQLite end-to-end matrix lives in
``tests/integration/auth/test_oauth_rfc7009_revocation.py``). Pins the RFC
7009 §2.1 lookup contract — the hint is only an ordering optimization, both
token types are always tried — and the decided semantics: access revoke kills
one token (grant survives), refresh revoke is the FULL disconnect through the
shared grant sweep (grant row + every grant token + the cause-in-data event).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from jentic_one.auth.services.oauth_revocation_service import (
    RFC7009_CLIENT_REVOCATION_REASON,
    OAuthRevocationService,
)

_CLIENT_ID = "oc_public_app"


def _make_ctx() -> tuple[MagicMock, AsyncMock]:
    ctx = MagicMock()
    mock_session = AsyncMock()

    async def _run_in_transaction(fn: object, **_kwargs: object) -> object:
        return await fn(mock_session)  # type: ignore[operator]

    ctx.admin_db.run_in_transaction = AsyncMock(side_effect=_run_in_transaction)
    return ctx, mock_session


def _access_row(
    *,
    oauth_client_id: str | None = _CLIENT_ID,
    oauth_grant_id: str | None = "ocg_1",
    revoked_at: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = "at_row_1"
    row.actor_id = "agnt_1"
    row.actor_type = "agent"
    row.token_family_id = "tfam_1"
    row.expires_at = datetime.now(UTC) + timedelta(hours=1)
    row.revoked_at = revoked_at
    row.oauth_client_id = oauth_client_id
    row.oauth_grant_id = oauth_grant_id
    return row


def _refresh_row(
    *,
    oauth_client_id: str | None = _CLIENT_ID,
    oauth_grant_id: str | None = "ocg_1",
    revoked_at: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = "rt_row_1"
    row.actor_id = "agnt_1"
    row.actor_type = "agent"
    row.token_family_id = "tfam_1"
    row.expires_at = datetime.now(UTC) + timedelta(days=7)
    row.revoked_at = revoked_at
    row.oauth_client_id = oauth_client_id
    row.oauth_grant_id = oauth_grant_id
    return row


def _grant_row(*, status: str = "active") -> MagicMock:
    row = MagicMock()
    row.id = "ocg_1"
    row.oauth_client_id = _CLIENT_ID
    row.agent_id = "agnt_1"
    row.user_id = "usr_1"
    row.status = status
    return row


_SVC = "jentic_one.auth.services.oauth_revocation_service"


# --- access-token arm: single token dies, grant survives --------------------


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_access_token_revoke_kills_single_token_only(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    ctx, _session = _make_ctx()
    at = _access_row()
    mock_at_repo.get_by_hash = AsyncMock(return_value=at)
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token(
        "at_tok", client_id=_CLIENT_ID, token_type_hint="access_token"
    )

    mock_at_repo.revoke.assert_awaited_once()
    # The grant survives an access-token revoke: no family/grant sweep runs.
    mock_at_repo.revoke_family.assert_not_called()
    mock_rt_repo.revoke_family.assert_not_called()
    mock_rt_repo.get_by_hash.assert_not_called()
    assert mock_audit.await_args is not None
    assert RFC7009_CLIENT_REVOCATION_REASON in mock_audit.await_args.kwargs["reason"]


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_access_token_foreign_client_is_noop(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    """Lineage mismatch: found-but-foreign must neither revoke, nor error, nor
    fall through to the refresh lookup (no oracle)."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=_access_row())
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token("at_tok", client_id="oc_other")

    mock_at_repo.revoke.assert_not_called()
    mock_rt_repo.get_by_hash.assert_not_called()
    mock_audit.assert_not_awaited()


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_platform_lineage_token_is_noop(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    """A platform token (no oauth_client_id) can never be revoked through the
    public-client arm, whatever client_id the caller claims."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=_access_row(oauth_client_id=None))
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token("at_tok", client_id=_CLIENT_ID)

    mock_at_repo.revoke.assert_not_called()
    mock_audit.assert_not_awaited()


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_already_revoked_access_token_is_noop(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=_access_row(revoked_at=datetime.now(UTC)))
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token("at_tok", client_id=_CLIENT_ID)

    mock_at_repo.revoke.assert_not_called()
    mock_audit.assert_not_awaited()


# --- refresh-token arm: the full disconnect ----------------------------------


@patch(f"{_SVC}.revoke_grant_and_sweep_tokens", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_refresh_token_revoke_is_full_disconnect(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_sweep: AsyncMock,
) -> None:
    """Refresh revoke runs THE shared sweep (grant row + grant tokens) with the
    rfc7009 cause-in-data reason, plus the §2.1 family belt."""
    ctx, session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)
    mock_rt_repo.get_by_hash = AsyncMock(return_value=_refresh_row())
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()
    grant = _grant_row()
    mock_grant_repo.get_by_id = AsyncMock(return_value=grant)

    await OAuthRevocationService(ctx).revoke_client_token(
        "rt_tok", client_id=_CLIENT_ID, token_type_hint="refresh_token"
    )

    mock_rt_repo.revoke_family.assert_awaited_once_with(session, "tfam_1")
    mock_at_repo.revoke_family.assert_awaited_once_with(session, "tfam_1")
    mock_sweep.assert_awaited_once()
    assert mock_sweep.await_args is not None
    assert mock_sweep.await_args.args[1] is grant
    assert mock_sweep.await_args.kwargs["event_reason"] == RFC7009_CLIENT_REVOCATION_REASON


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.revoke_grant_and_sweep_tokens", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_refresh_token_without_grant_revokes_family_only(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_sweep: AsyncMock,
    mock_audit: AsyncMock,
) -> None:
    """A grant-less confidential-lineage refresh token still gets the RFC 7009
    §2.1 family sweep; there is no grant row to disconnect."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)
    mock_rt_repo.get_by_hash = AsyncMock(return_value=_refresh_row(oauth_grant_id=None))
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token("rt_tok", client_id=_CLIENT_ID)

    mock_rt_repo.revoke_family.assert_awaited_once()
    mock_at_repo.revoke_family.assert_awaited_once()
    mock_sweep.assert_not_awaited()
    mock_grant_repo.get_by_id.assert_not_called()
    assert mock_audit.await_args is not None
    assert RFC7009_CLIENT_REVOCATION_REASON in mock_audit.await_args.kwargs["reason"]


@patch(f"{_SVC}.revoke_grant_and_sweep_tokens", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_refresh_token_foreign_client_is_noop(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_sweep: AsyncMock,
) -> None:
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)
    mock_rt_repo.get_by_hash = AsyncMock(return_value=_refresh_row())
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token("rt_tok", client_id="oc_other")

    mock_rt_repo.revoke_family.assert_not_called()
    mock_at_repo.revoke_family.assert_not_called()
    mock_sweep.assert_not_awaited()


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.revoke_grant_and_sweep_tokens", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_missing_client_id_is_always_noop(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_sweep: AsyncMock,
    mock_audit: AsyncMock,
) -> None:
    """No client_id → no lineage can match (auth method 'none' still binds the
    caller to its client_id) — nothing is ever revoked."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=_access_row())
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token("at_tok", client_id=None)

    mock_at_repo.revoke.assert_not_called()
    mock_sweep.assert_not_awaited()
    mock_audit.assert_not_awaited()


# --- RFC 7009 §2.1 hint semantics: ordering only, always fall through -------


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_wrong_hint_still_finds_access_token(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """hint=refresh_token on an access token: the refresh lookup misses and the
    search MUST extend to the access table (§2.1)."""
    ctx, _session = _make_ctx()
    mock_rt_repo.get_by_hash = AsyncMock(return_value=None)
    mock_at_repo.get_by_hash = AsyncMock(return_value=_access_row())
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token(
        "at_tok", client_id=_CLIENT_ID, token_type_hint="refresh_token"
    )

    mock_rt_repo.get_by_hash.assert_awaited_once()
    mock_at_repo.revoke.assert_awaited_once()


@patch(f"{_SVC}.revoke_grant_and_sweep_tokens", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_wrong_hint_still_finds_refresh_token(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_sweep: AsyncMock,
) -> None:
    """hint=access_token on a refresh token: falls through to the refresh
    table and still runs the full disconnect."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)
    mock_rt_repo.get_by_hash = AsyncMock(return_value=_refresh_row())
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()
    mock_grant_repo.get_by_id = AsyncMock(return_value=_grant_row())

    await OAuthRevocationService(ctx).revoke_client_token(
        "rt_tok", client_id=_CLIENT_ID, token_type_hint="access_token"
    )

    mock_at_repo.get_by_hash.assert_awaited_once()
    mock_sweep.assert_awaited_once()


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.OAuthClientGrantRepository")
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_unknown_hint_value_is_treated_as_no_hint(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """§2.1: an unknown hint value must not error (we handle both supported
    types, so ``unsupported_token_type`` is never applicable) — it degrades to
    the default lookup order."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=_access_row())
    mock_at_repo.revoke = AsyncMock()

    await OAuthRevocationService(ctx).revoke_client_token(
        "at_tok", client_id=_CLIENT_ID, token_type_hint="something_else"
    )

    mock_at_repo.revoke.assert_awaited_once()


@patch(f"{_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_SVC}.RefreshTokenRepository")
@patch(f"{_SVC}.AccessTokenRepository")
async def test_unknown_token_is_silent_noop(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    """Both lookups miss → no error, no audit (the route answers 200)."""
    ctx, _session = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)
    mock_rt_repo.get_by_hash = AsyncMock(return_value=None)

    await OAuthRevocationService(ctx).revoke_client_token("junk", client_id=_CLIENT_ID)

    mock_at_repo.get_by_hash.assert_awaited_once()
    mock_rt_repo.get_by_hash.assert_awaited_once()
    mock_audit.assert_not_awaited()
