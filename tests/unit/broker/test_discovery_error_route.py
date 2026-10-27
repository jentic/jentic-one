"""Discovery errors from the registry resolver, through the real execute ``_handle``.

The broker's in-process registry resolver raises the shared operation-discovery
errors (``shared/discovery/errors.py``) from ``resolve_operation``. These tests
stand a resolver stub (a protocol fake, not a DB mock) behind the real
``_handle`` and the real broker problem+json handlers, and assert each error
reaches the client as its own broker problem — never an unhandled 500 (#1962).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

import jentic_one.broker.web.routers.execute as execute_mod
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import ResolveResult
from jentic_one.shared.discovery.errors import (
    AmbiguousMatchError,
    DiscoveryError,
    MethodNotAllowedError,
    TooManyCandidatesError,
)

_PATH = "/api.example.com/v1/things"


class _RaisingResolver:
    """A registry resolver whose lookup fails with one discovery error."""

    def __init__(self, exc: DiscoveryError) -> None:
        self.exc = exc

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None:
        raise self.exc


@pytest.fixture(autouse=True)
def _passthrough_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the SSRF validator's DNS lookup; it is covered in ``test_url_validation.py``."""

    async def _validate(raw_url: str, egress: Any = None) -> str:
        return raw_url

    monkeypatch.setattr(execute_mod, "validate_upstream_url_async", _validate)


def _broker(exc: DiscoveryError) -> TestClient:
    app = FastAPI()
    install_broker_error_handlers(app)
    app.state.broker_registry_resolver = _RaisingResolver(exc)

    @app.api_route("/{upstream_url:path}", methods=["GET", "POST"])
    async def proxy(upstream_url: str, request: Request) -> Response:
        return await _handle(
            request,
            request.method,
            MagicMock(),  # ctx: never reached, discovery fails first
            Identity(sub="agnt_test", permissions=[]),
            MagicMock(),  # credential_deriver
            MagicMock(),  # agent_rule_evaluator
            MagicMock(),  # runner
            None,  # idempotency
        )

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("exc", "status", "problem_type"),
    [
        (AmbiguousMatchError(2), 409, "ambiguous_match"),
        (TooManyCandidatesError(), 503, "too_many_candidates"),
        (MethodNotAllowedError(["GET"]), 405, "method_not_allowed"),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
def test_resolver_discovery_error_is_a_broker_problem(
    exc: DiscoveryError, status: int, problem_type: str
) -> None:
    resp = _broker(exc).post(f"{_PATH}?token=secret")

    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers[JenticHeader.ERROR_ORIGIN.value] == "broker"
    body = resp.json()
    assert body["type"] == problem_type
    assert body["status"] == status
    assert body["error_origin"] == "broker"
    # The path names the occurrence; the query string (which can carry
    # secrets) is never echoed.
    assert body["instance"] == _PATH
    assert "secret" not in resp.text
