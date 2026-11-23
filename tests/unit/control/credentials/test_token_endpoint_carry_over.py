"""Unit tests for may_carry_refresh_token in token_endpoint.py."""

from __future__ import annotations

import pytest

from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.services.credentials.token_endpoint import may_carry_refresh_token


def _token_row(
    *,
    app_registration_id: str | None = "oar_1",
    encrypted_refresh_token: str | None = "enc-rt-1",
) -> OAuthToken:
    return OAuthToken(
        credential_id="cred_1",
        encrypted_access_token="enc-at-1",
        encrypted_refresh_token=encrypted_refresh_token,
        app_registration_id=app_registration_id,
        created_by="usr_1",
    )


@pytest.mark.parametrize("reg_id", ["oar_1", None])
def test_may_carry_refresh_token_when_same_client_and_confirmed_account(
    reg_id: str | None,
) -> None:
    existing = _token_row(app_registration_id=reg_id)
    assert (
        may_carry_refresh_token(
            existing,
            new_registration_id=reg_id,
            connected_as="alice",
            stored_account_ref="alice",
        )
        is True
    )


@pytest.mark.parametrize(
    ("existing_reg", "new_reg"),
    [
        ("oar_1", "oar_2"),
        ("oar_1", None),
        (None, "oar_1"),
    ],
)
def test_may_carry_refresh_token_rejects_different_registration(
    existing_reg: str | None, new_reg: str | None
) -> None:
    existing = _token_row(app_registration_id=existing_reg)
    assert (
        may_carry_refresh_token(
            existing,
            new_registration_id=new_reg,
            connected_as="alice",
            stored_account_ref="alice",
        )
        is False
    )


@pytest.mark.parametrize(
    ("connected_as", "stored_account_ref"),
    [
        (None, "alice"),
        (None, None),
        ("alice", None),
        ("bob", "alice"),
    ],
)
def test_may_carry_refresh_token_requires_matching_identity_echo(
    connected_as: str | None, stored_account_ref: str | None
) -> None:
    existing = _token_row(app_registration_id="oar_1")
    assert (
        may_carry_refresh_token(
            existing,
            new_registration_id="oar_1",
            connected_as=connected_as,
            stored_account_ref=stored_account_ref,
        )
        is False
    )


def test_may_carry_refresh_token_rejects_when_existing_lacks_refresh_token() -> None:
    existing = _token_row(app_registration_id="oar_1", encrypted_refresh_token=None)
    assert (
        may_carry_refresh_token(
            existing,
            new_registration_id="oar_1",
            connected_as="alice",
            stored_account_ref="alice",
        )
        is False
    )
