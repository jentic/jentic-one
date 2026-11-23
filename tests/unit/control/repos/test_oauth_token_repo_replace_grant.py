"""Unit tests for OAuthTokenRepository.replace_grant vs update_tokens."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.control.core.schema import ControlBase, Credential
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.credentials import StoredCredentialType

_CRED_ID = "cred_replace_grant_test"
_USER_ID = "usr_test"


@pytest.fixture()
async def sqlite_control_db() -> AsyncGenerator[DatabaseSession, None]:
    """Provide a real in-memory SQLite control database with schema created."""
    control_db = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
    await control_db.connect()
    async with control_db.engine.begin() as conn:
        await conn.run_sync(ControlBase.metadata.create_all)
    try:
        yield control_db
    finally:
        await control_db.close()


async def _seed_credential(control_db: DatabaseSession, credential_id: str = _CRED_ID) -> None:
    async with control_db.transaction() as session:
        session.add(
            Credential(
                id=credential_id,
                type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
                name="Test OAuth2",
                api_vendor="acme",
                api_name="widgets",
                api_version="v1",
                provider="direct_oauth2",
                created_by=_USER_ID,
            )
        )


async def test_replace_grant_clears_omitted_refresh_token_and_scope(
    sqlite_control_db: DatabaseSession,
) -> None:
    await _seed_credential(sqlite_control_db)
    old_expiry = datetime.now(UTC) + timedelta(hours=1)
    new_expiry = datetime.now(UTC) + timedelta(hours=2)

    async with sqlite_control_db.transaction() as session:
        await OAuthTokenRepository.create(
            session,
            credential_id=_CRED_ID,
            encrypted_access_token="enc-access-a",
            encrypted_refresh_token="enc-refresh-a",
            expires_at=old_expiry,
            scope="read write",
            app_registration_id="oar_1",
            created_by=_USER_ID,
        )
        await OAuthTokenRepository.mark_revoked(session, _CRED_ID)

    async with sqlite_control_db.transaction() as session:
        replaced = await OAuthTokenRepository.replace_grant(
            session,
            _CRED_ID,
            encrypted_access_token="enc-access-b",
            encrypted_refresh_token=None,
            expires_at=new_expiry,
            scope=None,
            app_registration_id=None,
        )
        assert replaced is not None

    async with sqlite_control_db.session() as session:
        row = await OAuthTokenRepository.get_by_credential(session, _CRED_ID)
        assert row is not None
        assert row.encrypted_access_token == "enc-access-b"
        assert row.encrypted_refresh_token is None
        assert row.scope is None
        assert row.app_registration_id is None
        assert row.expires_at == new_expiry
        assert row.revoked_at is None
        assert row.last_refreshed is not None


async def test_replace_grant_returns_none_when_row_missing(
    sqlite_control_db: DatabaseSession,
) -> None:
    async with sqlite_control_db.transaction() as session:
        result = await OAuthTokenRepository.replace_grant(
            session,
            "cred_nonexistent",
            encrypted_access_token="enc-access",
            encrypted_refresh_token=None,
            expires_at=None,
            scope=None,
            app_registration_id=None,
        )
    assert result is None


async def test_update_tokens_still_merges_for_refresh(
    sqlite_control_db: DatabaseSession,
) -> None:
    await _seed_credential(sqlite_control_db)
    expiry = datetime.now(UTC) + timedelta(hours=1)

    async with sqlite_control_db.transaction() as session:
        await OAuthTokenRepository.create(
            session,
            credential_id=_CRED_ID,
            encrypted_access_token="enc-access-a",
            encrypted_refresh_token="enc-refresh-a",
            expires_at=expiry,
            scope="read write",
            app_registration_id="oar_1",
            created_by=_USER_ID,
        )

    async with sqlite_control_db.transaction() as session:
        updated = await OAuthTokenRepository.update_tokens(
            session,
            _CRED_ID,
            encrypted_access_token="enc-access-refreshed",
            encrypted_refresh_token=None,
            expires_at=expiry,
            scope=None,
        )
        assert updated is not None

    async with sqlite_control_db.session() as session:
        row = await OAuthTokenRepository.get_by_credential(session, _CRED_ID)
        assert row is not None
        assert row.encrypted_access_token == "enc-access-refreshed"
        assert row.encrypted_refresh_token == "enc-refresh-a"
        assert row.scope == "read write"
        assert row.app_registration_id == "oar_1"
