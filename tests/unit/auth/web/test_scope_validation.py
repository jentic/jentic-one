"""Unit tests for shared OAuth/DCR scope token validation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from jentic_one.auth.web.schemas._scope import SCOPE_TOKEN_RE, validate_scope_string
from jentic_one.auth.web.schemas.oauth_client_registration import (
    OAuthClientRegistrationRequest,
)
from jentic_one.auth.web.schemas.registration import RegisterRequest


def _build_register_request(scope: str | None) -> RegisterRequest:
    return RegisterRequest(
        client_name="invoice-reconciler",
        jwks={"keys": []},
        scope=scope,
    )


def _build_oauth_client_request(scope: str | None) -> OAuthClientRegistrationRequest:
    return OAuthClientRegistrationRequest(
        client_name="Cursor",
        redirect_uris=["http://localhost:33418/callback"],
        scope=scope,
    )


def _validation_error_message(builder: Any, scope: str) -> str:
    with pytest.raises(ValidationError) as exc_info:
        builder(scope)
    errors = exc_info.value.errors()
    assert len(errors) == 1
    return str(errors[0]["msg"])


def test_scope_token_regex_and_valid_scopes() -> None:
    assert SCOPE_TOKEN_RE.pattern == r"^[a-zA-Z0-9_:./-]+$"
    assert validate_scope_string(None) is None
    valid = " ".join([f"scope:{i}" for i in range(100)])
    assert validate_scope_string(valid) == valid
    max_token = "a" * 64
    assert validate_scope_string(max_token) == max_token
    assert _build_register_request(valid).scope == valid
    assert _build_oauth_client_request(valid).scope == valid
    assert _build_register_request(None).scope is None
    assert _build_oauth_client_request(None).scope is None


def test_101_scope_tokens_rejected_with_identical_message() -> None:
    scope = " ".join(f"s{i}" for i in range(101))
    expected = "scope must contain at most 100 tokens"

    with pytest.raises(ValueError) as exc_info:
        validate_scope_string(scope)
    assert str(exc_info.value) == expected

    reg_msg = _validation_error_message(_build_register_request, scope)
    dcr_msg = _validation_error_message(_build_oauth_client_request, scope)
    assert reg_msg == f"Value error, {expected}"
    assert dcr_msg == reg_msg


def test_65_char_scope_token_rejected_with_identical_message() -> None:
    scope = "a" * 65
    expected = "each scope token must be at most 64 characters, got 65"

    with pytest.raises(ValueError) as exc_info:
        validate_scope_string(scope)
    assert str(exc_info.value) == expected

    reg_msg = _validation_error_message(_build_register_request, scope)
    dcr_msg = _validation_error_message(_build_oauth_client_request, scope)
    assert reg_msg == f"Value error, {expected}"
    assert dcr_msg == reg_msg


def test_invalid_characters_scope_token_rejected_with_identical_message() -> None:
    scope = "a b$"
    expected = "scope token 'b$' contains invalid characters"

    with pytest.raises(ValueError) as exc_info:
        validate_scope_string(scope)
    assert str(exc_info.value) == expected

    reg_msg = _validation_error_message(_build_register_request, scope)
    dcr_msg = _validation_error_message(_build_oauth_client_request, scope)
    assert reg_msg == f"Value error, {expected}"
    assert dcr_msg == reg_msg
