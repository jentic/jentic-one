"""Unregistered-URL seam: ``AppContainer.unregistered_url_handler``.

Proves the contract points of the seam (see
``jentic_one.shared.web.protocols.UnregisteredUrlHandler``):

- **Wiring**: a container with a handler stashes it on
  ``app.state.unregistered_url_handler`` for both ``create_surface_app`` and
  ``create_combined_app``; the default container leaves the attribute unset,
  so the router's ``getattr`` fallback preserves today's 404.
- **Selection**: ``broker/web/routers/execute.py::_handle_unregistered_url``
  returns ``None`` with no handler or a ``None``-returning handler — both fall
  through to ``OperationNotFoundError`` — and returns a handler's ``Response``
  verbatim.
- **Route branch** (through the real ``_handle``): a handler short-circuits the
  unregistered-URL miss with its own response; without a handler the miss
  raises ``operation_not_found`` exactly as today; the **pinned-revision** miss
  never invokes the handler (that miss is a caller pin error on a registered
  API, not an unregistered flow); and the handler receives the **validated**
  URL (the egress contract's ordering — hook after ``validate_upstream_url``).

The spy is a compliant ``UnregisteredUrlHandler``
(``jentic_one.testing.BaseUnregisteredUrlHandlerComplianceTest`` guards the
signature in ``tests/unit/testing/test_compliance_oss.py``); here we only
assert the wiring and selection behaviour. The SSRF validator itself is
unit-tested in ``test_url_validation.py`` — the route tests replace it with a
marker-returning fake precisely to prove the handler sees the validator's
*output*, not the raw reconstruction.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request, Response

import jentic_one.broker.web.routers.execute as execute_mod
from jentic_one.broker.core.exceptions import OperationNotFoundError
from jentic_one.broker.web.routers.execute import _handle, _handle_unregistered_url
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    ResolveResult,
    RevisionPinOutcome,
    RevisionPinResult,
)
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app
from jentic_one.shared.web.container import AppContainer
from jentic_one.shared.web.protocols import UnregisteredUrlHandler

_RAW_URL = "https://api.example.com/v1/things"
#: What the fake validator returns — distinct from the raw reconstruction so a
#: handler receiving it proves the hook runs *after* ``validate_upstream_url``.
_VALIDATED_URL = "https://validated.example.com/v1/things"


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
        request.app.state.unregistered_url_handler = handler
    return request


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def test_spy_handler_is_an_unregistered_url_handler() -> None:
    """Sanity: the spy honors the protocol (isinstance seam)."""
    assert isinstance(_SpyHandler(None), UnregisteredUrlHandler)


def test_container_stashes_handler_on_combined_app(ctx: Context) -> None:
    handler = _SpyHandler(None)
    container = AppContainer(ctx=ctx, unregistered_url_handler=handler)
    app = create_combined_app(ctx, ["control"], container=container)
    assert app.state.unregistered_url_handler is handler


def test_container_stashes_handler_on_surface_app(ctx: Context) -> None:
    handler = _SpyHandler(None)
    container = AppContainer(ctx=ctx, unregistered_url_handler=handler)
    app = create_surface_app(
        ctx,
        title="test-surface",
        routers=[],
        enabled_apps={"broker"},
        container=container,
    )
    assert app.state.unregistered_url_handler is handler


def test_default_container_leaves_handler_unset(ctx: Context) -> None:
    """No injection → the attribute is absent, so the router 404s as today."""
    app = create_combined_app(ctx, ["control"])
    assert getattr(app.state, "unregistered_url_handler", None) is None


@pytest.mark.asyncio
async def test_no_handler_falls_through_to_404() -> None:
    """Absent hook → None → ``_handle`` raises OperationNotFoundError as today."""
    handled = await _handle_unregistered_url(
        _request_with_state(None),
        method="GET",
        upstream_url=_RAW_URL,
        identity=_identity(),
    )
    assert handled is None


@pytest.mark.asyncio
async def test_handler_returning_none_falls_through_to_404() -> None:
    """A handler that declines (returns None) must not swallow the 404."""
    spy = _SpyHandler(None)
    handled = await _handle_unregistered_url(
        _request_with_state(spy),
        method="GET",
        upstream_url=_RAW_URL,
        identity=_identity(),
    )
    assert handled is None
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_handler_response_is_returned_verbatim() -> None:
    """A returned Response short-circuits — the router returns it as-is."""
    reply = Response(content=b"observed", status_code=200, media_type="text/plain")
    spy = _SpyHandler(reply)
    handled = await _handle_unregistered_url(
        _request_with_state(spy),
        method="POST",
        upstream_url=_RAW_URL,
        identity=_identity(),
    )
    assert handled is reply


@pytest.mark.asyncio
async def test_handler_receives_arguments_verbatim() -> None:
    """The handler sees exactly the URL, method, and resolved identity passed."""
    spy = _SpyHandler(None)
    identity = _identity()
    await _handle_unregistered_url(
        _request_with_state(spy),
        method="DELETE",
        upstream_url=f"{_RAW_URL}/42?x=1",
        identity=identity,
    )
    assert spy.calls == [
        {
            "method": "DELETE",
            "upstream_url": f"{_RAW_URL}/42?x=1",
            "identity": identity,
        }
    ]


# ---------------------------------------------------------------------------
# Route-branch tests: through the real ``_handle``.
# ---------------------------------------------------------------------------


class _MissResolver:
    """Discovery always misses — an unregistered METHOD+URL."""

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None:
        return None


class _PinnedMissResolver:
    """Discovery hits, the pin resolves, the pinned re-resolve misses.

    Exercises the *second* ``operation_not_found`` site — the caller pin error
    on a registered API — which must never invoke the handler.
    """

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None:
        if revision_id is None:
            return ResolveResult(
                operation_id="op-1",
                api=APIReference(vendor="acme", name="payments", version="v1"),
                path_params={},
            )
        return None

    async def resolve_revision_pin(
        self, *, vendor: str, name: str, version: str, rev_label: str, identity: Identity
    ) -> RevisionPinResult:
        return RevisionPinResult(outcome=RevisionPinOutcome.RESOLVED, revision_id=uuid.uuid4())


def _asgi_request(
    resolver: object, handler: object | None, headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    """A real ``Request`` over a real app — no FastAPI internals are mocked."""
    app = FastAPI()
    app.state.broker_registry_resolver = resolver
    if handler is not None:
        app.state.unregistered_url_handler = handler
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api.example.com/v1/things",
        "raw_path": b"/api.example.com/v1/things",
        "query_string": b"",
        "headers": headers or [],
        "app": app,
    }
    return Request(scope)


@pytest.fixture()
def _marker_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the SSRF validator with a marker-returning fake.

    The validator itself is covered by ``test_url_validation.py``; here the
    marker proves the handler receives the validator's *output* (the ordering
    half of the egress contract) without a DNS lookup in a unit test.
    """

    def _validate(raw_url: str, egress: Any = None) -> str:
        assert raw_url == _RAW_URL
        return _VALIDATED_URL

    monkeypatch.setattr(execute_mod, "validate_upstream_url", _validate)


async def _run_handle(request: Request) -> Response:
    return await _handle(
        request,
        "GET",
        MagicMock(),  # ctx — never reached: both paths stop at the miss
        _identity(),
        MagicMock(),  # deriver
        MagicMock(),  # rule_evaluator
        MagicMock(),  # runner
        None,  # idempotency
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_marker_validator")
async def test_route_handler_short_circuits_unregistered_url() -> None:
    """Unregistered URL + handler → the handler's response, carrying the
    validated URL (hook strictly after ``validate_upstream_url``)."""
    reply = Response(content=b"handled", status_code=200, media_type="text/plain")
    spy = _SpyHandler(reply)
    response = await _run_handle(_asgi_request(_MissResolver(), spy))
    assert response is reply
    assert spy.calls[0]["upstream_url"] == _VALIDATED_URL
    assert spy.calls[0]["method"] == "GET"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_marker_validator")
async def test_route_no_handler_raises_operation_not_found() -> None:
    """Unregistered URL, no handler → today's 404, same type and detail."""
    with pytest.raises(OperationNotFoundError) as exc_info:
        await _run_handle(_asgi_request(_MissResolver(), None))
    assert exc_info.value.type == "operation_not_found"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_marker_validator")
async def test_route_pinned_revision_miss_never_invokes_handler() -> None:
    """The pinned-revision miss raises the same problem type but bypasses the
    handler — it is a caller pin error on a *registered* API."""
    spy = _SpyHandler(Response(content=b"must not be returned"))
    request = _asgi_request(
        _PinnedMissResolver(),
        spy,
        headers=[(b"jentic-revision", b"acme:payments:v1=rev_abc123")],
    )
    with pytest.raises(OperationNotFoundError) as exc_info:
        await _run_handle(request)
    assert exc_info.value.type == "operation_not_found"
    assert spy.calls == []
