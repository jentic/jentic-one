"""Unit tests for OAuth client service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.services.errors import (
    ConflictError,
    InvalidInputError,
    OAuthClientNotFoundError,
)
from jentic_one.admin.services.oauth_client_service import (
    OAuthClientService,
    _validate_redirect_uris,
)
from jentic_one.shared.models.audit import AuditAction


def test_accepts_valid_https_uris() -> None:
    _validate_redirect_uris(["https://example.com/callback"])


def test_accepts_multiple_https_uris() -> None:
    _validate_redirect_uris(
        [
            "https://example.com/callback",
            "https://app.example.org/oauth/redirect",
        ]
    )


def test_accepts_http_localhost() -> None:
    _validate_redirect_uris(["http://localhost:3000/callback"])


def test_accepts_http_127_0_0_1() -> None:
    _validate_redirect_uris(["http://127.0.0.1:8080/cb"])


def test_accepts_http_ipv6_loopback() -> None:
    """RFC 8252 §7.3: native-app clients must be able to use either loopback form."""
    _validate_redirect_uris(["http://[::1]:8080/cb"])


def test_rejects_http_non_localhost() -> None:
    with pytest.raises(InvalidInputError, match="http redirect_uri only allowed for localhost"):
        _validate_redirect_uris(["http://example.com/callback"])


def test_rejects_http_remote_host() -> None:
    with pytest.raises(InvalidInputError, match="http redirect_uri only allowed for localhost"):
        _validate_redirect_uris(["http://192.168.1.1/callback"])


def test_rejects_empty_list() -> None:
    with pytest.raises(InvalidInputError, match="at least one redirect_uri is required"):
        _validate_redirect_uris([])


def test_rejects_uri_without_scheme() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["example.com/callback"])


def test_rejects_uri_without_netloc() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["https://"])


def test_rejects_ftp_scheme() -> None:
    with pytest.raises(InvalidInputError, match="redirect_uri must use https or http"):
        _validate_redirect_uris(["ftp://example.com/callback"])


def test_rejects_javascript_scheme() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["javascript:alert(1)"])


# ---------- verify_client_secret ----------


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_verify_client_secret_rejects_inactive_client(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
) -> None:
    """An inactive client is rejected even if the secret is correct."""
    ctx = _make_ctx()
    client_row = MagicMock()
    client_row.active = False
    client_row.approval_status = "approved"
    client_row.client_secret_hash = "hash"
    mock_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = OAuthClientService(ctx)
    result = await svc.verify_client_secret("oc_test", "secret")

    assert result is False
    mock_verify.assert_awaited_once()


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_verify_client_secret_accepts_active_client(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
) -> None:
    """An active client with correct secret is accepted."""
    ctx = _make_ctx()
    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.client_secret_hash = "hash"
    mock_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    result = await svc.verify_client_secret("oc_test", "secret")

    assert result is True


@pytest.mark.parametrize("approval_status", ["pending", "denied"])
@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_verify_client_secret_rejects_unapproved_client(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
    approval_status: str,
) -> None:
    """The D7 gate: pending/denied clients fail even with the correct secret,
    and the dummy verify still runs (timing-uniform)."""
    ctx = _make_ctx()
    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = approval_status
    client_row.client_secret_hash = "hash"
    mock_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    result = await svc.verify_client_secret("oc_test", "secret")

    assert result is False
    mock_verify.assert_awaited_once()


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_verify_client_secret_null_hash_short_circuits_to_equalizer(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
) -> None:
    """A public (NULL-hash) row hits the dummy-hash equalizer and fails —
    'public' must not be distinguishable from wrong-secret by timing (D5)."""
    ctx = _make_ctx()
    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.client_secret_hash = None
    mock_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    result = await svc.verify_client_secret("oc_public", "any-secret")

    assert result is False
    mock_verify.assert_awaited_once()


# ---------- authenticate_for_token_endpoint ----------


def _client_row(
    *,
    active: bool = True,
    approval_status: str = "approved",
    auth_method: str = "client_secret_basic",
    secret_hash: str | None = "hash",
) -> MagicMock:
    row = MagicMock()
    row.active = active
    row.approval_status = approval_status
    row.token_endpoint_auth_method = auth_method
    row.client_secret_hash = secret_hash
    return row


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_public_client_without_secret_passes(
    mock_repo: MagicMock, mock_verify: MagicMock
) -> None:
    """Public client (auth method 'none'), no secret supplied → authenticated (D5)."""
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(
        return_value=_client_row(auth_method="none", secret_hash=None)
    )

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_public", None) is True
    mock_verify.assert_not_awaited()


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_public_client_with_secret_is_rejected_loudly(
    mock_repo: MagicMock, mock_verify: MagicMock
) -> None:
    """A supplied secret on a public client is a loud misconfiguration →
    rejected, and the dummy verify runs (timing-uniform)."""
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(
        return_value=_client_row(auth_method="none", secret_hash=None)
    )

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_public", "stray-secret") is False
    mock_verify.assert_awaited_once()


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_confidential_client_requires_and_verifies_secret(
    mock_repo: MagicMock, mock_verify: MagicMock
) -> None:
    """Confidential path unchanged: secret required, argon2-verified against the row."""
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(return_value=_client_row())
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_conf", "good-secret") is True
    mock_verify.assert_awaited_once_with("good-secret", "hash")


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_confidential_client_without_secret_is_rejected(
    mock_repo: MagicMock, mock_verify: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(return_value=_client_row())

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_conf", None) is False
    mock_verify.assert_not_awaited()


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_wrong_secret_is_rejected(
    mock_repo: MagicMock, mock_verify: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(return_value=_client_row())
    mock_verify.return_value = False

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_conf", "wrong") is False


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_unknown_client_with_secret_hits_equalizer(
    mock_repo: MagicMock, mock_verify: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(return_value=None)

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_missing", "secret") is False
    mock_verify.assert_awaited_once()


@pytest.mark.parametrize("supplied_secret", [None, "any-secret"])
@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_confidential_row_with_null_hash_fails_closed(
    mock_repo: MagicMock, mock_verify: MagicMock, supplied_secret: str | None
) -> None:
    """An invariant-violating row (confidential method, NULL hash) must NOT be
    treated as a public client: no-secret and with-secret both fail closed,
    and the with-secret path keeps the argon2 timing profile (§4.1)."""
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(
        return_value=_client_row(auth_method="client_secret_basic", secret_hash=None)
    )
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_broken", supplied_secret) is False
    if supplied_secret:
        mock_verify.assert_awaited_once()
    else:
        mock_verify.assert_not_awaited()


@pytest.mark.parametrize("supplied_secret", [None, "any-secret"])
@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_none_row_with_stray_hash_fails_closed(
    mock_repo: MagicMock, mock_verify: MagicMock, supplied_secret: str | None
) -> None:
    """The mirror violation ('none' method carrying a hash) also fails closed
    on both arms — the row disagrees with itself, so nothing authenticates."""
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(
        return_value=_client_row(auth_method="none", secret_hash="stray-hash")
    )
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    assert await svc.authenticate_for_token_endpoint("oc_broken", supplied_secret) is False


@pytest.mark.parametrize("approval_status", ["pending", "denied"])
@pytest.mark.parametrize(
    ("auth_method", "secret_hash", "supplied_secret"),
    [
        ("client_secret_basic", "hash", "good-secret"),
        ("none", None, None),
    ],
)
@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_token_auth_unapproved_client_fails_closed(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
    auth_method: str,
    secret_hash: str | None,
    supplied_secret: str | None,
    approval_status: str,
) -> None:
    """Pending/denied clients cannot authenticate — public or confidential (D7)."""
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(
        return_value=_client_row(
            approval_status=approval_status,
            auth_method=auth_method,
            secret_hash=secret_hash,
        )
    )
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    result = await svc.authenticate_for_token_endpoint("oc_gated", supplied_secret)
    assert result is False


# ---------- is_public_client ----------


@pytest.mark.parametrize(
    ("active", "approval_status", "auth_method", "expected"),
    [
        (True, "approved", "none", True),
        (True, "approved", "client_secret_basic", False),
        (False, "approved", "none", False),
        (True, "pending", "none", False),
        (True, "denied", "none", False),
    ],
)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_is_public_client_matrix(
    mock_repo: MagicMock,
    active: bool,
    approval_status: str,
    auth_method: str,
    expected: bool,
) -> None:
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(
        return_value=_client_row(
            active=active, approval_status=approval_status, auth_method=auth_method
        )
    )

    svc = OAuthClientService(ctx)
    assert await svc.is_public_client("oc_x") is expected


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_is_public_client_unknown_id_is_false(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    mock_repo.get_by_client_id = AsyncMock(return_value=None)

    svc = OAuthClientService(ctx)
    assert await svc.is_public_client("oc_missing") is False


# ---------- rotate_secret ----------


def _make_transactional_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_identity() -> MagicMock:
    identity = MagicMock()
    identity.sub = "usr_admin"
    identity.actor_type = "user"
    identity.origin.value = "api"
    return identity


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service._hash_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_rotate_secret_returns_new_plaintext_and_audits(
    mock_repo: MagicMock,
    mock_hash: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_transactional_ctx()
    mock_hash.return_value = "new_hash"
    mock_repo.get_by_id = AsyncMock(return_value=_client_row())
    mock_repo.update_secret_hash = AsyncMock(return_value=MagicMock(id="oac_1"))

    svc = OAuthClientService(ctx)
    new_secret = await svc.rotate_secret("oac_1", identity=_make_identity())

    assert isinstance(new_secret, str) and len(new_secret) > 0
    mock_repo.update_secret_hash.assert_awaited_once()
    mock_audit.assert_awaited_once()
    # Audit records the update action; secret plaintext must never appear.
    assert mock_audit.await_args is not None
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["target_id"] == "oac_1"
    assert kwargs["after"] == {"secret_rotated": True}
    assert new_secret not in str(kwargs)


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_rotate_secret_raises_when_not_found(mock_repo: MagicMock) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=None)
    mock_repo.update_secret_hash = AsyncMock(return_value=None)

    svc = OAuthClientService(ctx)
    with pytest.raises(OAuthClientNotFoundError):
        await svc.rotate_secret("oac_missing", identity=_make_identity())


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_rotate_secret_rejected_for_public_client(mock_repo: MagicMock) -> None:
    """A public (secret-less) client has nothing to rotate."""
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=_client_row(auth_method="none", secret_hash=None))
    mock_repo.update_secret_hash = AsyncMock()

    svc = OAuthClientService(ctx)
    with pytest.raises(InvalidInputError, match="public clients have no secret"):
        await svc.rotate_secret("oac_public", identity=_make_identity())
    mock_repo.update_secret_hash.assert_not_awaited()


# ---------- deactivate ----------


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_deactivate_records_before_snapshot(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    ctx = _make_transactional_ctx()
    existing = MagicMock()
    existing.name = "before"
    existing.description = "d"
    existing.redirect_uris = ["https://x.example.com/cb"]
    existing.active = True
    existing.require_consent = True
    existing.allowed_scopes = ["capabilities:read"]
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.deactivate = AsyncMock(return_value=True)

    svc = OAuthClientService(ctx)
    await svc.deactivate("oac_1", identity=_make_identity())

    assert mock_audit.await_args is not None
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["before"]["active"] is True
    assert kwargs["after"] == {"active": False}


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_deactivate_raises_when_repo_reports_no_update(
    mock_repo: MagicMock,
) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_repo.deactivate = AsyncMock(return_value=False)

    svc = OAuthClientService(ctx)
    with pytest.raises(OAuthClientNotFoundError):
        await svc.deactivate("oac_1", identity=_make_identity())


# ---------- update: allowed_scopes reset sentinel ----------


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_wildcard_scope_triggers_reset(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    ctx = _make_transactional_ctx()
    existing = MagicMock()
    existing.name = "before"
    existing.description = None
    existing.redirect_uris = ["https://x.example.com/cb"]
    existing.active = True
    existing.require_consent = True
    existing.allowed_scopes = ["capabilities:read"]
    updated = MagicMock()
    updated.name = "before"
    updated.description = None
    updated.redirect_uris = ["https://x.example.com/cb"]
    updated.active = True
    updated.require_consent = True
    updated.allowed_scopes = None
    updated.token_endpoint_auth_method = "client_secret_basic"
    updated.consent_model = "user"
    updated.registration_source = "admin"
    updated.software_id = None
    updated.approval_status = "approved"
    updated.id = "oac_1"
    updated.client_id = "oc_1"
    updated.created_at = datetime.now(UTC)
    updated.updated_at = None
    updated.created_by = "usr"
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.update = AsyncMock(return_value=updated)

    svc = OAuthClientService(ctx)
    await svc.update("oac_1", allowed_scopes=["*"], identity=_make_identity())

    assert mock_repo.update.await_args is not None
    kwargs = mock_repo.update.await_args.kwargs
    assert kwargs["reset_allowed_scopes"] is True
    assert kwargs["allowed_scopes"] is None


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_empty_scopes_is_deny_all(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    """allowed_scopes=[] means deny-all, not reset."""
    ctx = _make_transactional_ctx()
    existing = MagicMock()
    existing.allowed_scopes = ["capabilities:read"]
    existing.name = "n"
    existing.description = None
    existing.redirect_uris = ["https://x.example.com/cb"]
    existing.active = True
    existing.require_consent = True
    existing.token_endpoint_auth_method = "client_secret_basic"
    existing.consent_model = "user"
    existing.registration_source = "admin"
    existing.software_id = None
    existing.approval_status = "approved"
    existing.id = "oac_1"
    existing.client_id = "oc_1"
    existing.created_at = datetime.now(UTC)
    existing.updated_at = None
    existing.created_by = "usr"
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.update = AsyncMock(return_value=existing)

    svc = OAuthClientService(ctx)
    await svc.update("oac_1", allowed_scopes=[], identity=_make_identity())

    assert mock_repo.update.await_args is not None
    kwargs = mock_repo.update.await_args.kwargs
    assert kwargs["reset_allowed_scopes"] is False
    assert kwargs["allowed_scopes"] == []


# ---------- update: PATCH cannot manufacture denied/pending + active (D7) ----------


@pytest.mark.parametrize("approval_status", ["pending", "denied"])
@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_rejects_activation_of_unapproved_client(
    mock_repo: MagicMock, mock_audit: AsyncMock, approval_status: str
) -> None:
    """PATCH active=true on a non-approved row is a state-machine conflict:
    :approve is the only path back to active (D7, PR #1218 MAJOR-2)."""
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(
        return_value=_full_row(approval_status=approval_status, active=False)
    )
    mock_repo.update = AsyncMock()

    svc = OAuthClientService(ctx)
    with pytest.raises(ConflictError, match=":approve"):
        await svc.update("oac_1", active=True, identity=_make_identity())

    mock_repo.update.assert_not_awaited()
    mock_audit.assert_not_awaited()


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_allows_deactivation_of_denied_client(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    """active=false is always allowed — it only moves the row fail-closed."""
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=_full_row(approval_status="denied", active=False))
    mock_repo.update = AsyncMock(return_value=_full_row(approval_status="denied", active=False))

    svc = OAuthClientService(ctx)
    view = await svc.update("oac_1", active=False, identity=_make_identity())

    assert view.active is False
    mock_repo.update.assert_awaited_once()


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_allows_reactivation_of_approved_client(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    """The kill-switch round trip: approved + deactivated → PATCH active=true
    re-enables (the D7 'approved → deactivated → reactivated' leg)."""
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=_full_row(active=False))
    mock_repo.update = AsyncMock(return_value=_full_row(active=True))

    svc = OAuthClientService(ctx)
    view = await svc.update("oac_1", active=True, identity=_make_identity())

    assert view.active is True
    mock_repo.update.assert_awaited_once()


# ---------- create: public vs confidential ----------


def _full_row(**overrides: object) -> MagicMock:
    """A MagicMock OAuthClient row carrying every field ``_to_view`` reads."""
    row = MagicMock()
    row.id = "oac_1"
    row.client_id = "oc_1"
    row.name = "app"
    row.description = None
    row.redirect_uris = ["https://x.example.com/cb"]
    row.allowed_scopes = None
    row.active = True
    row.require_consent = True
    row.token_endpoint_auth_method = "client_secret_basic"
    row.consent_model = "user"
    row.registration_source = "admin"
    row.software_id = None
    row.approval_status = "approved"
    row.created_at = datetime.now(UTC)
    row.updated_at = None
    row.created_by = "usr_admin"
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service._hash_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_create_public_client_stores_no_secret(
    mock_repo: MagicMock, mock_hash: MagicMock, _mock_audit: AsyncMock
) -> None:
    """token_endpoint_auth_method='none' → no secret generated, hashed, or returned."""
    ctx = _make_transactional_ctx()
    mock_repo.create = AsyncMock(return_value=_full_row(token_endpoint_auth_method="none"))

    svc = OAuthClientService(ctx)
    result = await svc.create(
        name="public-app",
        redirect_uris=["https://x.example.com/cb"],
        token_endpoint_auth_method="none",
        identity=_make_identity(),
    )

    assert result.client_secret is None
    mock_hash.assert_not_called()
    assert mock_repo.create.await_args is not None
    kwargs = mock_repo.create.await_args.kwargs
    assert kwargs["client_secret_hash"] is None
    assert kwargs["token_endpoint_auth_method"] == "none"


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service._hash_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_create_confidential_client_keeps_secret_behavior(
    mock_repo: MagicMock, mock_hash: MagicMock, _mock_audit: AsyncMock
) -> None:
    """Default create is unchanged: secret generated, hashed, and returned once."""
    ctx = _make_transactional_ctx()
    mock_hash.return_value = "hashed"
    mock_repo.create = AsyncMock(return_value=_full_row())

    svc = OAuthClientService(ctx)
    result = await svc.create(
        name="conf-app",
        redirect_uris=["https://x.example.com/cb"],
        identity=_make_identity(),
    )

    assert isinstance(result.client_secret, str) and len(result.client_secret) > 0
    mock_hash.assert_called_once()
    assert mock_repo.create.await_args is not None
    kwargs = mock_repo.create.await_args.kwargs
    assert kwargs["client_secret_hash"] == "hashed"


async def test_create_rejects_unknown_auth_method() -> None:
    svc = OAuthClientService(_make_transactional_ctx())
    with pytest.raises(InvalidInputError, match="token_endpoint_auth_method"):
        await svc.create(
            name="x",
            redirect_uris=["https://x.example.com/cb"],
            token_endpoint_auth_method="private_key_jwt",
            identity=_make_identity(),
        )


async def test_create_rejects_unknown_consent_model() -> None:
    svc = OAuthClientService(_make_transactional_ctx())
    with pytest.raises(InvalidInputError, match="consent_model"):
        await svc.create(
            name="x",
            redirect_uris=["https://x.example.com/cb"],
            consent_model="org",
            identity=_make_identity(),
        )


# ---------- approve / deny lifecycle (D7) ----------


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_approve_sets_status_and_active_and_audits(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=_full_row(approval_status="pending", active=False))
    mock_repo.set_approval_status = AsyncMock(return_value=_full_row())

    svc = OAuthClientService(ctx)
    view = await svc.approve("oac_1", identity=_make_identity())

    assert view.approval_status == "approved"
    assert view.active is True
    mock_repo.set_approval_status.assert_awaited_once_with(
        ANY, "oac_1", approval_status="approved", active=True
    )
    assert mock_audit.await_args is not None
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["action"] == AuditAction.APPROVE
    assert kwargs["before"]["approval_status"] == "pending"
    assert kwargs["after"] == {"approval_status": "approved", "active": True}


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_deny_sets_status_inactive_and_audits_reason(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=_full_row(approval_status="pending"))
    mock_repo.set_approval_status = AsyncMock(
        return_value=_full_row(approval_status="denied", active=False)
    )

    svc = OAuthClientService(ctx)
    view = await svc.deny("oac_1", reason="untrusted vendor", identity=_make_identity())

    assert view.approval_status == "denied"
    assert view.active is False
    mock_repo.set_approval_status.assert_awaited_once_with(
        ANY, "oac_1", approval_status="denied", active=False
    )
    assert mock_audit.await_args is not None
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["action"] == AuditAction.DENY
    assert kwargs["reason"] == "untrusted vendor"
    assert kwargs["after"] == {"approval_status": "denied", "active": False}


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_deny_then_approve_is_reversible(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    """Deny keeps the row; a later approve restores approved+active (D7)."""
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=_full_row(approval_status="denied", active=False))
    mock_repo.set_approval_status = AsyncMock(return_value=_full_row())

    svc = OAuthClientService(ctx)
    view = await svc.approve("oac_1", identity=_make_identity())

    assert view.approval_status == "approved"
    assert view.active is True


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_approve_missing_client_raises(mock_repo: MagicMock) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=None)

    svc = OAuthClientService(ctx)
    with pytest.raises(OAuthClientNotFoundError):
        await svc.approve("oac_missing", identity=_make_identity())


# ---------- list_all approval_status filter ----------
#
# The list/get read paths also fold in the §4.8 active-grant counts, so the
# grant repo is patched alongside the client repo (returning no counts).


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientGrantRepository")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_list_all_passes_approval_status_filter(
    mock_repo: MagicMock, mock_grant_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_repo.list_all = AsyncMock(return_value=[])
    mock_grant_repo.count_active_by_client = AsyncMock(return_value={})

    svc = OAuthClientService(ctx)
    await svc.list_all(include_inactive=True, approval_status="pending")

    mock_repo.list_all.assert_awaited_once_with(
        ANY, include_inactive=True, approval_status="pending"
    )


@pytest.mark.parametrize("approval_status", ["pending", "denied"])
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientGrantRepository")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_list_all_pending_or_denied_filter_implies_include_inactive(
    mock_repo: MagicMock, mock_grant_repo: MagicMock, approval_status: str
) -> None:
    """Pending/denied rows are active=false by construction (D7); the approval
    queue must not need the caller to also pass include_inactive=true."""
    ctx = _make_ctx()
    mock_repo.list_all = AsyncMock(return_value=[])
    mock_grant_repo.count_active_by_client = AsyncMock(return_value={})

    svc = OAuthClientService(ctx)
    await svc.list_all(approval_status=approval_status)

    mock_repo.list_all.assert_awaited_once_with(
        ANY, include_inactive=True, approval_status=approval_status
    )


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientGrantRepository")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_list_all_approved_filter_keeps_active_default(
    mock_repo: MagicMock, mock_grant_repo: MagicMock
) -> None:
    """Filtering on approved keeps today's active-only default."""
    ctx = _make_ctx()
    mock_repo.list_all = AsyncMock(return_value=[])
    mock_grant_repo.count_active_by_client = AsyncMock(return_value={})

    svc = OAuthClientService(ctx)
    await svc.list_all(approval_status="approved")

    mock_repo.list_all.assert_awaited_once_with(
        ANY, include_inactive=False, approval_status="approved"
    )


async def test_list_all_rejects_unknown_approval_status() -> None:
    svc = OAuthClientService(_make_ctx())
    with pytest.raises(InvalidInputError, match="approval_status"):
        await svc.list_all(approval_status="bogus")
