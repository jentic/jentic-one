"""MCP ``me`` (whoami) for a fallback-resolved ``sva_`` identity (theme-8 Phase 2, M-1).

The service-account surface and ``ServiceAccountService`` are gone; an
unmigrated ``sak_`` key still resolves as the SA through the Phase-1 resolver
fallback, and the MCP ``me`` tool must answer coherently for it through the
shared raw-SQL read (``LegacyServiceAccountIdentityService``), exactly like the
REST ``/me`` branch.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp import types as mcp_types

import jentic_one.mcp.tools as tools_mod
from jentic_one.auth.services.errors import ActorNotFoundError
from jentic_one.auth.services.schemas.legacy_service_account import (
    LegacyServiceAccountIdentityView,
)
from jentic_one.mcp.envelopes import CODE_NOT_AUTHENTICATED, ToolError
from jentic_one.mcp.tools import CallEnv, handle_whoami
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
            sub="sva_legacy",
            permissions=["capabilities:execute"],
            actor_type=ActorType.SERVICE_ACCOUNT,
        ),
        credential="sak_unmigrated",
        base_url="https://auth.example.com",
        session_id=None,
    )


class _FakeLegacySvc:
    view: LegacyServiceAccountIdentityView | None = None

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def get_self(self, identity: Identity) -> LegacyServiceAccountIdentityView:
        if _FakeLegacySvc.view is None:
            raise ActorNotFoundError(identity.sub)
        return _FakeLegacySvc.view


@pytest.fixture(autouse=True)
def _patch_legacy_svc(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLegacySvc.view = None
    monkeypatch.setattr(tools_mod, "LegacyServiceAccountIdentityService", _FakeLegacySvc)


async def test_me_answers_for_fallback_resolved_service_account() -> None:
    _FakeLegacySvc.view = LegacyServiceAccountIdentityView(
        id="sva_legacy",
        name="legacy-sa",
        status="active",
        registered_by="usr_owner",
        approved_by="usr_admin",
        scopes=["capabilities:execute", "capabilities:read"],
    )

    result = await handle_whoami(_env(), {})

    (content,) = result.content
    assert isinstance(content, mcp_types.TextContent)
    payload = json.loads(content.text)
    assert payload["type"] == "service_account"
    assert payload["id"] == "sva_legacy"
    assert payload["name"] == "legacy-sa"
    assert payload["scopes"] == ["capabilities:execute", "capabilities:read"]
    assert payload["token_scopes"] == ["capabilities:execute"]
    assert payload["registered_by"] == "usr_owner"
    assert payload["approved_by"] == "usr_admin"


async def test_me_missing_service_account_row_is_not_authenticated() -> None:
    with pytest.raises(ToolError) as exc_info:
        await handle_whoami(_env(), {})
    assert exc_info.value.code == CODE_NOT_AUTHENTICATED
