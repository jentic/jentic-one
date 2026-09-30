"""The dispatch catch-all never returns an unexpected exception's text to the agent.

An exception message can carry request material (headers, credential values);
the tool result names only the exception class, and the server-side log keeps a
redacted copy.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest
import structlog.testing

import jentic_one.mcp.tools as tools_mod
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType

_SECRET = "SECRET_IN_EXCEPTION_TEXT"


async def _boom(_env: CallEnv, _arguments: dict[str, Any]) -> Any:
    raise RuntimeError(f"Illegal header value b'{_SECRET}' (Authorization: Bearer {_SECRET})")


async def test_unexpected_failure_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tools_mod.HANDLERS, "whoami", _boom)
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    env = CallEnv(
        ctx=ctx,
        identity=Identity(sub="agnt_1", permissions=[], actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )

    with structlog.testing.capture_logs() as logs:
        result = await dispatch_tool_call(env, "whoami", {})

    assert result.is_error
    (content,) = result.content
    assert isinstance(content, mcp_types.TextContent)
    payload = json.loads(content.text)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["error"] == "unexpected failure (RuntimeError)"
    assert _SECRET not in content.text

    (entry,) = [e for e in logs if e["event"] == "mcp_tool_unexpected_failure"]
    assert entry["tool"] == "whoami"
    assert entry["error_type"] == "RuntimeError"
    assert "Bearer ***REDACTED***" in entry["error"]
