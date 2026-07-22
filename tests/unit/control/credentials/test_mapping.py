"""Unit tests for wire ↔ stored credential type mapping."""

from __future__ import annotations

from jentic_one.control.services.credentials.mapping import is_refreshable, to_stored, to_wire
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType


def test_to_stored_bearer_token() -> None:
    assert to_stored(CredentialType.BEARER_TOKEN) == StoredCredentialType.STATIC_BEARER_TOKEN


def test_to_stored_api_key() -> None:
    assert to_stored(CredentialType.API_KEY) == StoredCredentialType.API_KEY


def test_to_stored_basic() -> None:
    assert to_stored(CredentialType.BASIC) == StoredCredentialType.BASIC_AUTH


def test_to_stored_oauth2_client_credentials() -> None:
    assert (
        to_stored(CredentialType.OAUTH2, grant_type="client_credentials")
        == StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS
    )


def test_to_stored_oauth2_default_grant_type() -> None:
    assert to_stored(CredentialType.OAUTH2) == StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS


def test_to_stored_oauth2_authorization_code() -> None:
    assert (
        to_stored(CredentialType.OAUTH2, grant_type="authorization_code")
        == StoredCredentialType.OAUTH2_AUTHORIZATION_CODE
    )


def test_to_wire_static_bearer_token() -> None:
    assert to_wire(StoredCredentialType.STATIC_BEARER_TOKEN) == CredentialType.BEARER_TOKEN


def test_to_wire_session_token() -> None:
    assert to_wire(StoredCredentialType.SESSION_TOKEN) == CredentialType.BEARER_TOKEN


def test_to_wire_api_key() -> None:
    assert to_wire(StoredCredentialType.API_KEY) == CredentialType.API_KEY


def test_to_wire_basic_auth() -> None:
    assert to_wire(StoredCredentialType.BASIC_AUTH) == CredentialType.BASIC


def test_to_wire_oauth2_client_credentials() -> None:
    assert to_wire(StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS) == CredentialType.OAUTH2


def test_to_wire_oauth2_authorization_code() -> None:
    assert to_wire(StoredCredentialType.OAUTH2_AUTHORIZATION_CODE) == CredentialType.OAUTH2


def test_to_wire_oauth2_implicit() -> None:
    assert to_wire(StoredCredentialType.OAUTH2_IMPLICIT) == CredentialType.OAUTH2


def test_to_wire_no_auth() -> None:
    # NO_AUTH is now a supported wire type (#603): a no-auth credential is a
    # marker that the API needs no secret. It must round-trip, not raise.
    assert to_wire(StoredCredentialType.NO_AUTH) == CredentialType.NO_AUTH


def test_to_stored_no_auth() -> None:
    assert to_stored(CredentialType.NO_AUTH) == StoredCredentialType.NO_AUTH


def test_is_refreshable_no_auth() -> None:
    assert is_refreshable(StoredCredentialType.NO_AUTH) is False


def test_is_refreshable_oauth2_client_credentials() -> None:
    assert is_refreshable(StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS) is True


def test_is_refreshable_oauth2_authorization_code() -> None:
    assert is_refreshable(StoredCredentialType.OAUTH2_AUTHORIZATION_CODE) is True


def test_is_refreshable_static_bearer_token() -> None:
    assert is_refreshable(StoredCredentialType.STATIC_BEARER_TOKEN) is False


def test_is_refreshable_api_key() -> None:
    assert is_refreshable(StoredCredentialType.API_KEY) is False


def test_is_refreshable_basic_auth() -> None:
    assert is_refreshable(StoredCredentialType.BASIC_AUTH) is False
