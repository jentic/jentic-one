"""Request-validation 422 bodies never echo the submitted values.

FastAPI's default 422 body copies each offending ``input`` back to the caller,
so a malformed credential request would reflect its secret. The shared handler
(installed by ``create_surface_app`` / ``create_combined_app``) and the broker's
problem+json handler both strip ``input`` and value-bearing ``ctx``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from jentic_one.broker.web.errors import handle_validation
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.control.web.deps import get_credential_service
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web import deps as shared_deps
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.errors import (
    request_validation_error_handler,
    sanitize_validation_errors,
)

_SECRET = "SECRET_LEAK_422_VALUE"


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


class _Body(BaseModel):
    name: str
    count: int
    code: str

    @field_validator("code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        raise ValueError(f"bad code {v}")


def test_sanitize_drops_input_and_value_bearing_ctx() -> None:
    errors = [
        {
            "type": "string_too_short",
            "loc": ("body", "name"),
            "msg": "String should have at least 3 characters",
            "input": _SECRET,
            "ctx": {"min_length": 3},
            "url": "https://errors.pydantic.dev/2/v/string_too_short",
        },
        {
            "type": "value_error",
            "loc": ("body", "code"),
            "msg": "Value error, bad",
            "input": {"key": _SECRET},
            "ctx": {"error": ValueError(_SECRET)},
        },
    ]

    out = sanitize_validation_errors(errors)

    assert _SECRET not in json.dumps(out)
    assert out[0] == {
        "type": "string_too_short",
        "loc": ["body", "name"],
        "msg": "String should have at least 3 characters",
        "ctx": {"min_length": 3},
        "url": "https://errors.pydantic.dev/2/v/string_too_short",
    }
    assert out[1] == {"type": "value_error", "loc": ["body", "code"], "msg": "Value error, bad"}


def test_shared_handler_keeps_default_shape_without_input() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]

    @app.post("/things")
    async def create(body: _Body) -> dict[str, str]:  # pragma: no cover - never reached
        return {}

    resp = TestClient(app).post("/things", json={"name": _SECRET, "count": _SECRET})

    assert resp.status_code == 422
    assert _SECRET not in resp.text
    detail = resp.json()["detail"]
    assert {tuple(err["loc"]) for err in detail} == {("body", "count"), ("body", "code")}
    assert all("input" not in err for err in detail)


@pytest.mark.asyncio
async def test_broker_validation_handler_strips_input() -> None:
    exc = RequestValidationError(
        [{"type": "missing", "loc": ("body", "x"), "msg": "Field required", "input": _SECRET}]
    )

    resp = await handle_validation(MagicMock(), exc)

    body = json.loads(bytes(resp.body))
    assert resp.status_code == 422
    assert body["title"] == "Request validation failed"
    assert body["type"] == "about:blank#validation"
    assert body["errors"] == [{"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]
    assert _SECRET not in bytes(resp.body).decode()


def test_combined_app_422_does_not_echo_input(ctx: Context) -> None:
    app = create_combined_app(ctx, ["admin", "control", "auth"])
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/auth/login", json={"password": _SECRET})

    assert resp.status_code == 422
    assert _SECRET not in resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", "email"]


@pytest.mark.parametrize(
    ("method", "path", "payload", "field"),
    [
        ("POST", "/credentials", {"type": "bogus", "key": _SECRET}, None),
        (
            "POST",
            "/credentials",
            {
                "type": "api_key",
                "name": "k",
                "api": {"vendor": "example.com"},
                "key": f"{_SECRET}\n",
                "location": "header",
                "field_name": "X-Api-Key",
            },
            "key",
        ),
        (
            "PATCH",
            "/credentials/cred_1",
            {"type": "bearer_token", "token": f"{_SECRET}\r\nX-Injected: 1"},
            "token",
        ),
    ],
)
def test_control_app_credential_422_does_not_echo_secret(
    ctx: Context, method: str, path: str, payload: dict[str, Any], field: str | None
) -> None:
    app = create_control_app(ctx)
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: Identity(
        sub="usr_alice", permissions=["credentials:write"]
    )
    # Never reached: body validation fails before the handler runs.
    app.dependency_overrides[get_credential_service] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.request(method, path, json=payload)

    assert resp.status_code == 422, resp.text
    assert _SECRET not in resp.text
    if field is not None:
        msgs = [err["msg"] for err in resp.json()["detail"]]
        assert any(f"{field} must not contain control characters" in m for m in msgs), msgs
