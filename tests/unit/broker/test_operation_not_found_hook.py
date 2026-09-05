"""Discovery-miss hook seam: ``AppContainer.on_operation_not_found``.

Proves the three contract points of the seam (see
``jentic_one.shared.broker.protocols.OperationNotFoundHandler``):

- **Wiring**: a container with a handler stashes it on
  ``app.state.on_operation_not_found`` for both ``create_surface_app`` and
  ``create_combined_app``; the default container leaves the attribute unset,
  so the router's ``getattr`` fallback preserves today's 404.
- **Selection**: ``broker/web/routers/execute.py::_handle_discovery_miss`` (the
  helper ``_handle`` calls at the unregistered-URL miss) returns ``None`` with
  no handler or a ``None``-returning handler — both fall through to
  ``OperationNotFoundError`` — and returns a handler's ``Response`` verbatim.
- **Arguments**: the handler receives exactly the egress-validated URL, the
  method, and the resolved identity (the load-bearing line of the contract).

The spy is a compliant ``OperationNotFoundHandler``
(``jentic_one.testing.BaseOperationNotFoundHandlerComplianceTest`` guards the
signature in ``tests/unit/testing/test_compliance_oss.py``); here we only
assert the wiring and selection behaviour.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import Request, Response

from jentic_one.broker.web.routers.execute import _handle_discovery_miss
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import OperationNotFoundHandler
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app
from jentic_one.shared.web.container import AppContainer


class _SpyHandler:
    """A minimal compliant handler that records its calls and replies fixed."""

    def __init__(self, reply: Response | None) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, *, method: str, upstream_url: str, identity: Identity, request: Request
    ) -> Response | None:
        self.calls.append({"method": method, "upstream_url": upstream_url, "identity": identity})
        return self.reply


def _identity() -> Identity:
    return Identity(sub="agnt_test", permissions=[])


def _request_with_state(handler: object | None) -> Any:
    request = MagicMock()
    if handler is None:
        # A state object with NO attribute — the getattr fallback path, i.e.
        # a default container where the factory never stashed the hook.
        request.app.state = object()
    else:
        request.app.state.on_operation_not_found = handler
    return request


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def test_spy_handler_is_an_operation_not_found_handler() -> None:
    """Sanity: the spy honors the protocol (isinstance seam)."""
    assert isinstance(_SpyHandler(None), OperationNotFoundHandler)


def test_container_stashes_handler_on_combined_app(ctx: Context) -> None:
    handler = _SpyHandler(None)
    container = AppContainer(ctx=ctx, on_operation_not_found=handler)
    app = create_combined_app(ctx, ["control"], container=container)
    assert app.state.on_operation_not_found is handler


def test_container_stashes_handler_on_surface_app(ctx: Context) -> None:
    handler = _SpyHandler(None)
    container = AppContainer(ctx=ctx, on_operation_not_found=handler)
    app = create_surface_app(
        ctx,
        title="test-surface",
        routers=[],
        enabled_apps={"broker"},
        container=container,
    )
    assert app.state.on_operation_not_found is handler


def test_default_container_leaves_handler_unset(ctx: Context) -> None:
    """No injection → the attribute is absent, so the router 404s as today."""
    app = create_combined_app(ctx, ["control"])
    assert getattr(app.state, "on_operation_not_found", None) is None


@pytest.mark.asyncio
async def test_no_handler_falls_through_to_404() -> None:
    """Absent hook → None → ``_handle`` raises OperationNotFoundError as today."""
    handled = await _handle_discovery_miss(
        _request_with_state(None),
        method="GET",
        upstream_url="https://api.example.com/v1/things",
        identity=_identity(),
    )
    assert handled is None


@pytest.mark.asyncio
async def test_handler_returning_none_falls_through_to_404() -> None:
    """A handler that declines (returns None) must not swallow the 404."""
    spy = _SpyHandler(None)
    handled = await _handle_discovery_miss(
        _request_with_state(spy),
        method="GET",
        upstream_url="https://api.example.com/v1/things",
        identity=_identity(),
    )
    assert handled is None
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_handler_response_is_returned_verbatim() -> None:
    """A returned Response short-circuits — the router returns it as-is."""
    reply = Response(content=b"observed", status_code=200, media_type="text/plain")
    spy = _SpyHandler(reply)
    handled = await _handle_discovery_miss(
        _request_with_state(spy),
        method="POST",
        upstream_url="https://api.example.com/v1/things",
        identity=_identity(),
    )
    assert handled is reply


@pytest.mark.asyncio
async def test_handler_receives_validated_url_and_identity() -> None:
    """The handler sees exactly the egress-validated URL — the SSRF contract's
    load-bearing argument — plus the method and resolved identity."""
    spy = _SpyHandler(None)
    identity = _identity()
    await _handle_discovery_miss(
        _request_with_state(spy),
        method="DELETE",
        upstream_url="https://api.example.com/v1/things/42?x=1",
        identity=identity,
    )
    assert spy.calls == [
        {
            "method": "DELETE",
            "upstream_url": "https://api.example.com/v1/things/42?x=1",
            "identity": identity,
        }
    ]
