"""Credential values injected into upstream requests reject control characters.

``key`` / ``token`` / ``field_name`` (and the sigv4 header-bound fields) are
written verbatim into outbound headers or query parameters, so CR/LF and other
C0 controls are refused at the request edge — and the error names the field,
never the value.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from jentic_one.control.web.schemas.credentials import (
    ApiKeyCreateRequest,
    ApiKeyUpdateRequest,
    BearerTokenCreateRequest,
    BearerTokenUpdateRequest,
    Sigv4CreateRequest,
    Sigv4UpdateRequest,
)

_SECRET = "SECRET_VALUE_123"
_API = {"vendor": "example.com"}

_BASE: dict[type[BaseModel], dict[str, Any]] = {
    BearerTokenCreateRequest: {"type": "bearer_token", "name": "t", "api": _API, "token": "tok"},
    BearerTokenUpdateRequest: {"type": "bearer_token"},
    ApiKeyCreateRequest: {
        "type": "api_key",
        "name": "k",
        "api": _API,
        "key": "k",
        "location": "header",
        "field_name": "X-Api-Key",
    },
    ApiKeyUpdateRequest: {"type": "api_key"},
    Sigv4CreateRequest: {
        "type": "sigv4",
        "name": "s",
        "api": _API,
        "access_key_id": "AKIA",
        "secret_access_key": "sk",
        "aws_region": "us-east-1",
        "aws_service": "s3",
    },
    Sigv4UpdateRequest: {"type": "sigv4"},
}

_CASES = [
    (model, field)
    for model, fields in (
        (BearerTokenCreateRequest, ("token",)),
        (BearerTokenUpdateRequest, ("token",)),
        (ApiKeyCreateRequest, ("key", "field_name")),
        (ApiKeyUpdateRequest, ("key", "field_name")),
        (Sigv4CreateRequest, ("access_key_id", "session_token", "aws_region", "aws_service")),
        (Sigv4UpdateRequest, ("access_key_id", "session_token", "aws_region", "aws_service")),
    )
    for field in fields
]


@pytest.mark.parametrize(("model", "field"), _CASES)
@pytest.mark.parametrize("bad", ["\n", "\r\n", "\x00", "\t", "\x7f"])
def test_control_characters_rejected_without_echoing_value(
    model: type[BaseModel], field: str, bad: str
) -> None:
    payload = {**_BASE[model], field: f"{_SECRET}{bad}"}

    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(payload)

    (err,) = excinfo.value.errors()
    assert err["loc"] == (field,)
    assert f"{field} must not contain control characters" in err["msg"]
    assert _SECRET not in err["msg"]


@pytest.mark.parametrize(("model", "field"), _CASES)
def test_ordinary_values_accepted(model: type[BaseModel], field: str) -> None:
    value = "Bearer-ish_value.with/punct+=~ and spaces"
    parsed = model.model_validate({**_BASE[model], field: value})
    assert getattr(parsed, field) == value
