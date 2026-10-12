"""Integration tests for the typed-secret row writers against a real control DB.

Each helper must fill the secret row of a credential that already exists (a
pending credential, as the connect flow creates one) with the same encryption
path ``CredentialService.create`` uses, and only inside the caller's
transaction.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from pydantic import SecretStr
from sqlalchemy import delete

from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.repos import (
    BasicCredentialRepository,
    CustomerAPIKeyRepository,
    Sigv4CredentialRepository,
    TokenValueCredentialRepository,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    ApiKeyFull,
    BasicAuthFull,
    BearerTokenFull,
    Sigv4Full,
)
from jentic_one.control.services.credentials.typed_secrets import (
    write_api_key_secret,
    write_basic_secret,
    write_bearer_token_secret,
    write_sigv4_secret,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.credentials import StoredCredentialType

pytestmark = pytest.mark.integration

_CREATOR = "usr_typed_secret"


async def _wipe(control_db: DatabaseSession) -> None:
    async with control_db.session() as session:
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(BasicCredential))
        await session.execute(delete(Sigv4Credential))
        await session.execute(delete(Credential))
        await session.commit()


@pytest.fixture()
async def clean_credentials(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Ensure the credential and typed-secret tables are empty around each test."""
    await _wipe(control_db)
    yield
    await _wipe(control_db)


async def _seed_pending(control_db: DatabaseSession, credential_id: str, type_: str) -> None:
    """Insert a secret-less pending credential with an explicit id (SQLite has no KSUID)."""
    async with control_db.transaction() as session:
        session.add(
            Credential(
                id=credential_id,
                type=type_,
                name=f"pending {credential_id}",
                api_vendor="example-com",
                state="pending",
                created_by=_CREATOR,
            )
        )


async def test_write_bearer_token_secret_fills_existing_credential(
    integration_context: Context, control_db: DatabaseSession, clean_credentials: None
) -> None:
    """The bearer row is keyed to the existing credential and round-trips through decrypt."""
    encryption = integration_context.encryption
    await _seed_pending(
        control_db, "cred_ts_bearer", StoredCredentialType.STATIC_BEARER_TOKEN.value
    )

    async with control_db.transaction() as session:
        echo = await write_bearer_token_secret(
            session,
            encryption,
            credential_id="cred_ts_bearer",
            token=SecretStr("tok-abcdef123"),
            created_by=_CREATOR,
        )
    assert echo == BearerTokenFull(token="tok-abcdef123")

    async with control_db.session() as session:
        row = await TokenValueCredentialRepository.get_by_credential(session, "cred_ts_bearer")
    assert row is not None
    assert row.encrypted_token_value != "tok-abcdef123"
    assert encryption.decrypt(row.encrypted_token_value) == "tok-abcdef123"
    assert row.token_preview == "…123"
    assert row.created_by == _CREATOR


async def test_write_api_key_secret_fills_existing_credential(
    integration_context: Context, control_db: DatabaseSession, clean_credentials: None
) -> None:
    """The API key row keeps its injection point and round-trips through decrypt."""
    encryption = integration_context.encryption
    await _seed_pending(control_db, "cred_ts_apikey", StoredCredentialType.API_KEY.value)

    async with control_db.transaction() as session:
        echo = await write_api_key_secret(
            session,
            encryption,
            credential_id="cred_ts_apikey",
            key=SecretStr("sk_live_xyz789"),
            location="query",
            field_name="api_key",
            created_by=_CREATOR,
        )
    assert echo == ApiKeyFull(key="sk_live_xyz789", location="query", field_name="api_key")

    async with control_db.session() as session:
        row = await CustomerAPIKeyRepository.get_by_credential(session, "cred_ts_apikey")
    assert row is not None
    assert encryption.decrypt(row.encrypted_key) == "sk_live_xyz789"
    assert row.key_preview == "…789"
    assert row.location == "query"
    assert row.field_name == "api_key"
    assert row.created_by == _CREATOR


async def test_write_basic_secret_fills_existing_credential(
    integration_context: Context, control_db: DatabaseSession, clean_credentials: None
) -> None:
    """The basic row stores the username in clear and the password encrypted."""
    encryption = integration_context.encryption
    await _seed_pending(control_db, "cred_ts_basic", StoredCredentialType.BASIC_AUTH.value)

    async with control_db.transaction() as session:
        echo = await write_basic_secret(
            session,
            encryption,
            credential_id="cred_ts_basic",
            username="alice",
            password=SecretStr("hunter2!"),
            created_by=_CREATOR,
        )
    assert echo == BasicAuthFull(username="alice", password="hunter2!")

    async with control_db.session() as session:
        row = await BasicCredentialRepository.get_by_credential(session, "cred_ts_basic")
    assert row is not None
    assert row.username == "alice"
    assert encryption.decrypt(row.encrypted_password) == "hunter2!"
    assert row.created_by == _CREATOR


@pytest.mark.parametrize(
    ("session_token", "expect_stored"),
    [("sess-token-1", True), ("", False), (None, False)],
)
async def test_write_sigv4_secret_fills_existing_credential(
    integration_context: Context,
    control_db: DatabaseSession,
    clean_credentials: None,
    session_token: str | None,
    expect_stored: bool,
) -> None:
    """The SigV4 row round-trips; an empty or absent session token stores none."""
    encryption = integration_context.encryption
    await _seed_pending(control_db, "cred_ts_sigv4", StoredCredentialType.AWS_SIGV4.value)

    async with control_db.transaction() as session:
        echo = await write_sigv4_secret(
            session,
            encryption,
            credential_id="cred_ts_sigv4",
            access_key_id="AKIAEXAMPLE",
            secret_access_key=SecretStr("wJalrXUtnFEMIabc"),
            session_token=None if session_token is None else SecretStr(session_token),
            region="eu-west-1",
            service="s3",
            created_by=_CREATOR,
        )
    assert echo == Sigv4Full(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="wJalrXUtnFEMIabc",
        session_token=session_token,
        aws_region="eu-west-1",
        aws_service="s3",
    )

    async with control_db.session() as session:
        row = await Sigv4CredentialRepository.get_by_credential(session, "cred_ts_sigv4")
    assert row is not None
    assert row.access_key_id == "AKIAEXAMPLE"
    assert encryption.decrypt(row.encrypted_secret_access_key) == "wJalrXUtnFEMIabc"
    assert row.secret_preview == "…abc"
    assert row.region == "eu-west-1"
    assert row.service == "s3"
    if expect_stored:
        assert row.encrypted_session_token is not None
        assert encryption.decrypt(row.encrypted_session_token) == session_token
    else:
        assert row.encrypted_session_token is None


async def test_typed_secret_write_rolls_back_with_caller_transaction(
    integration_context: Context, control_db: DatabaseSession, clean_credentials: None
) -> None:
    """The helper only flushes: a caller rollback leaves no secret row behind."""
    await _seed_pending(control_db, "cred_ts_rollback", StoredCredentialType.API_KEY.value)

    with pytest.raises(RuntimeError, match="abort"):
        async with control_db.transaction() as session:
            await write_api_key_secret(
                session,
                integration_context.encryption,
                credential_id="cred_ts_rollback",
                key=SecretStr("sk_rollback_000"),
                location="header",
                field_name="X-Api-Key",
                created_by=_CREATOR,
            )
            raise RuntimeError("abort")

    async with control_db.session() as session:
        row = await CustomerAPIKeyRepository.get_by_credential(session, "cred_ts_rollback")
    assert row is None
