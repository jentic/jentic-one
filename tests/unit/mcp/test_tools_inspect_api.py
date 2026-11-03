"""inspect_api on the mount — the Go handler's arms, replayed.

Mirrors ``cli/internal/cli/api/connect_options.go``: the API identity reaches
the service as given, the result is the route's wire shape plus the next
step, and an API the registry lacks points at ``import_api``.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

import jentic_one.mcp.tools as tools_mod
from jentic_one.control.services.integrations.connect_session_service import (
    ApiTarget,
    ConnectOptions,
    ConnectSchemeOption,
    ConnectScopeOption,
)
from jentic_one.control.services.integrations.errors import UnknownApiError
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType


def _env() -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(
            sub="agnt_1", permissions=["credentials:connect"], actor_type=ActorType.AGENT
        ),
        credential="jak_test",
        base_url="https://one.example.com",
        session_id=None,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


class _FakeService:
    calls: ClassVar[list[tuple[ApiTarget, str | None]]] = []
    missing: ClassVar[bool] = False

    def __init__(self, ctx: Any, **seams: Any) -> None:
        pass

    async def connect_options(
        self, target: ApiTarget, *, registration_id: str | None = None
    ) -> ConnectOptions:
        if _FakeService.missing:
            raise UnknownApiError(target.vendor, target.name, target.version)
        _FakeService.calls.append((target, registration_id))
        scope = ConnectScopeOption(
            name="gmail.send",
            description="Send",
            classification="write",
            default=False,
            requestable=True,
        )
        return ConnectOptions(
            vendor=target.vendor,
            name=target.name,
            version=target.version,
            display_name="Gmail",
            api_target_supported=True,
            schemes=[
                ConnectSchemeOption(
                    name="oauth",
                    type="oauth2",
                    kind="oauth2",
                    flow="authorization_code",
                    vendor_key="gmail",
                    registration_id=None,
                    scopes=[scope],
                )
            ],
        )


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeService.calls = []
    _FakeService.missing = False
    monkeypatch.setattr(tools_mod, "ConnectSessionService", _FakeService)


async def test_the_api_identity_reaches_the_service_and_the_next_step_is_added() -> None:
    result = await dispatch_tool_call(
        _env(), "inspect_api", {"api": "googleapis.com/gmail/v1", "registration_id": "oar_1"}
    )
    assert not result.is_error, result.content
    ((target, registration_id),) = _FakeService.calls
    assert (target, registration_id) == (ApiTarget("googleapis.com", "gmail", "v1"), "oar_1")
    payload = _payload(result)
    (scheme,) = payload["schemes"]
    assert scheme["scopes"][0] == {
        "name": "gmail.send",
        "description": "Send",
        "classification": "write",
        "default": False,
        "requestable": True,
    }
    assert "request_connection" in payload["next_step"]


async def test_an_api_the_registry_lacks_points_at_import() -> None:
    _FakeService.missing = True
    result = await dispatch_tool_call(_env(), "inspect_api", {"api": "nope/nope/v0"})
    assert result.is_error
    assert _payload(result)["next_tool"] == "import_api"
