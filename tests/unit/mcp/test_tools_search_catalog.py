"""search_catalog on the mount — envelope passthrough and the catalog-search error arms.

Mirrors ``cli/internal/cli/api/mcp_catalog_test.go``'s search_catalog coverage:
the optional ``match`` evidence a ranking strategy may attach is passed through
only when present (default-ranking and browse entries keep their exact shape),
a later page whose ranking can't serve it (503 ``catalog_search_unavailable``
on the HTTP route) is a retryable TRANSPORT_ERROR with the same wording as the
Go handler, and a bad or stale cursor is a correctable invalid-params error.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.mcp.envelopes import CODE_TRANSPORT_ERROR
from jentic_one.mcp.tools import (
    CATALOG_SEARCH_UNAVAILABLE_ACTIONABLE,
    CATALOG_SEARCH_UNAVAILABLE_MESSAGE,
    CallEnv,
    dispatch_tool_call,
)
from jentic_one.registry.services.catalog.search import CatalogMatch
from jentic_one.registry.services.catalog.service import CatalogEntryView, CatalogListView
from jentic_one.registry.services.errors import (
    CatalogSearchUnavailableError,
    StaleCatalogCursorError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.pagination import MAX_CATALOG_CURSOR_LENGTH, InvalidCursorError


def _env() -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(
            sub="agnt_1", permissions=["capabilities:read"], actor_type=ActorType.AGENT
        ),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


def _view(api_id: str, match: CatalogMatch | None = None) -> CatalogEntryView:
    return CatalogEntryView(
        api_id=api_id,
        vendor=api_id,
        path=f"apis/openapi/{api_id}",
        spec_url=f"https://example.test/{api_id}/openapi.json",
        github_url=None,
        registered=False,
        match=match,
    )


class _FakeCatalogService:
    """CatalogService stand-in: serves one canned page (or raises)."""

    page: ClassVar[CatalogListView | None] = None
    error: ClassVar[Exception | None] = None
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def list_all(self, **kwargs: Any) -> CatalogListView:
        _FakeCatalogService.calls.append(kwargs)
        if _FakeCatalogService.error is not None:
            raise _FakeCatalogService.error
        assert _FakeCatalogService.page is not None
        return _FakeCatalogService.page


@pytest.fixture()
def catalog(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCatalogService]:
    _FakeCatalogService.page = None
    _FakeCatalogService.error = None
    _FakeCatalogService.calls = []
    monkeypatch.setattr(tools_mod, "CatalogService", _FakeCatalogService)
    return _FakeCatalogService


async def test_match_is_passed_through_only_when_present(
    catalog: type[_FakeCatalogService],
) -> None:
    catalog.page = CatalogListView(
        data=[_view("pay.com", CatalogMatch(("alias", "future_channel"))), _view("stripe.com")],
        catalog_total=2,
        registered_count=0,
        manifest_age_seconds=None,
        has_more=False,
        next_cursor=None,
    )

    result = await dispatch_tool_call(_env(), "search_catalog", {"query": "pay"})

    assert not result.is_error
    with_match, without = _payload(result)["data"]
    assert with_match["match"] == {"channels": ["alias", "future_channel"]}
    assert "match" not in without


async def test_later_page_strategy_failure_is_a_retryable_transport_error(
    catalog: type[_FakeCatalogService],
) -> None:
    catalog.error = CatalogSearchUnavailableError("semantic")

    result = await dispatch_tool_call(_env(), "search_catalog", {"query": "pay", "cursor": "page2"})

    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == CODE_TRANSPORT_ERROR
    assert payload["retryable"] is True
    assert payload["error"] == CATALOG_SEARCH_UNAVAILABLE_MESSAGE
    assert payload["actionable_step"] == CATALOG_SEARCH_UNAVAILABLE_ACTIONABLE
    assert "semantic" not in json.dumps(payload)
    assert "next_tool" not in payload


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (StaleCatalogCursorError(), "without a cursor"),
        (InvalidCursorError("Invalid pagination cursor"), "invalid pagination cursor"),
    ],
)
async def test_bad_or_stale_cursor_is_invalid_params(
    catalog: type[_FakeCatalogService], error: Exception, hint: str
) -> None:
    catalog.error = error
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_search_catalog(_env(), {"query": "pay", "cursor": "c"})
    assert hint in str(err.value)


async def test_over_long_cursor_is_invalid_params_before_the_service(
    catalog: type[_FakeCatalogService],
) -> None:
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_search_catalog(
            _env(), {"query": "pay", "cursor": "A" * (MAX_CATALOG_CURSOR_LENGTH + 1)}
        )
    assert "invalid pagination cursor" in str(err.value)
    assert catalog.calls == []
