"""Unit tests for the OAuth callback route's response contract.

The /credentials/oauth/callback endpoint 302/303-redirects the popup the SPA
opened to a public SPA route (`/app/oauth/connected?status=ok|error`) rather
than emitting HTML from the API. The parent SPA learns whether the connect
actually succeeded by polling `GET /credentials/{id}`, not from this redirect.
These tests pin:

  * success redirects to `/app/oauth/connected?status=ok`,
  * every error branch redirects to `/app/oauth/connected?status=error`,
  * the service is invoked iff `state` is present (a missing state means
    the URL was hand-typed/probed — the IdP always echoes state),
  * no error branch leaks provider-side detail into the redirect URL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Starlette's TestClient rides httpx2 when it is installed (it is — the mcp
# SDK depends on it), so client responses are httpx2 types here.
from httpx2 import Response

from jentic_one.control.services.credentials.connect_service import (
    ConnectFlowError,
    ConnectService,
)
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.web.deps import get_connect_service
from jentic_one.control.web.routers import credentials as credentials_router
from jentic_one.shared.web.static import SPA_MOUNT_PATH

# Public SPA route the popup is redirected to (under the /app SPA mount).
_RETURN_PATH = "/app/oauth/connected"

# Substring the redirect URL MUST NEVER contain (any of these would be a
# regression — they carry implementation/protocol/provider detail).
_FORBIDDEN_LEAK_FRAGMENTS = (
    "nonce",
    "code",
    "cred_",
    "provider",
    "token",
    "reason",
)


def _build_app(*, complete_mock: AsyncMock) -> FastAPI:
    """Build a minimal FastAPI app exposing only the credentials router."""
    app = FastAPI()
    app.include_router(credentials_router.router)

    fake_service = AsyncMock(spec=ConnectService)
    fake_service.complete = complete_mock

    app.dependency_overrides[get_connect_service] = lambda: fake_service
    return app


def _location(response: Response) -> str:
    assert response.status_code in (302, 303), response.status_code
    location = response.headers["location"]
    # No body on the API origin — the redirect is the whole response.
    assert not response.text.strip()
    return location


def _assert_redirect_status(response: Response, expected_status: str) -> None:
    """Redirect target is the SPA return route with the expected status."""
    location = _location(response)
    parts = urlsplit(location)
    assert parts.path == _RETURN_PATH, location
    query = parse_qs(parts.query)
    assert query.get("status") == [expected_status], location
    # The only param is `status` — nothing else rides along.
    assert set(query) == {"status"}, location
    # Defence in depth: no protocol/provider detail anywhere in the URL.
    lower = location.lower()
    for fragment in _FORBIDDEN_LEAK_FRAGMENTS:
        assert fragment not in lower, f"redirect leaked {fragment!r}: {location!r}"


def _get(app: FastAPI, params: dict[str, str]) -> Response:
    # Inspect the redirect itself, not the (nonexistent) SPA target.
    with TestClient(app, follow_redirects=False) as tc:
        response: Response = tc.get("/credentials/oauth/callback", params=params)
    return response


def test_oauth_callback_redirects_ok_on_success() -> None:
    complete = AsyncMock(return_value="cred_123")
    app = _build_app(complete_mock=complete)

    response = _get(app, {"state": "valid-state", "code": "the-code"})

    _assert_redirect_status(response, "ok")
    complete.assert_awaited_once()


def test_oauth_callback_redirects_error_when_state_missing() -> None:
    complete = AsyncMock()
    app = _build_app(complete_mock=complete)

    response = _get(app, {"code": "abc"})

    _assert_redirect_status(response, "error")
    # The IdP always echoes state — a missing state means the URL was
    # hand-typed or probed. Short-circuit before the service.
    complete.assert_not_called()


def test_oauth_callback_redirects_error_on_connect_flow_error() -> None:
    complete = AsyncMock(side_effect=ConnectFlowError("nonce already redeemed"))
    app = _build_app(complete_mock=complete)

    response = _get(app, {"state": "valid-state", "code": "the-code"})

    _assert_redirect_status(response, "error")


def test_oauth_callback_redirects_error_on_credential_not_found() -> None:
    complete = AsyncMock(side_effect=CredentialNotFoundError("cred_missing"))
    app = _build_app(complete_mock=complete)

    response = _get(app, {"state": "valid-state", "code": "the-code"})

    _assert_redirect_status(response, "error")


def test_oauth_callback_redirects_error_on_provider_error() -> None:
    complete = AsyncMock(side_effect=ProviderError("token exchange failed"))
    app = _build_app(complete_mock=complete)

    response = _get(app, {"state": "valid-state", "code": "the-code"})

    _assert_redirect_status(response, "error")


@pytest.mark.parametrize(
    "query",
    [
        {"code": None, "error": "access_denied", "state": None},
        {"code": None, "error": None, "state": None},
    ],
)
def test_oauth_callback_missing_state_does_not_invoke_service(
    query: dict[str, str | None],
) -> None:
    """`state` is the only required param. Any callback without it is
    short-circuited before the service runs.
    """
    complete = AsyncMock()
    app = _build_app(complete_mock=complete)

    params = {k: v for k, v in query.items() if v is not None}

    response = _get(app, params)

    _assert_redirect_status(response, "error")
    complete.assert_not_called()


def test_return_path_is_derived_from_spa_mount_path() -> None:
    """The popup return path MUST be derived from ``SPA_MOUNT_PATH``, never
    hand-written.

    The SPA is mounted under ``SPA_MOUNT_PATH`` (``/app``); the backend callback
    redirects the popup to ``<SPA_MOUNT_PATH>/oauth/connected``. If someone
    changes the mount path (or hard-codes the return path), these two silently
    desync and the popup lands on a 404 instead of the self-closing page. Pin
    the derivation so that can't happen without this test failing.
    """
    assert f"{SPA_MOUNT_PATH}/oauth/connected" == credentials_router._CONNECT_RETURN_PATH
    # And it must sit under the SPA mount (the only origin that serves the
    # self-closing return page) — not at the API root.
    assert credentials_router._CONNECT_RETURN_PATH.startswith(f"{SPA_MOUNT_PATH}/")


def test_redirect_target_tracks_spa_mount_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repointing the SPA mount moves the redirect target with it.

    A behavioural companion to the static-derivation check: rebind the module's
    return path as if ``SPA_MOUNT_PATH`` were ``/console`` and confirm the live
    redirect follows. Guards against a future refactor that reads the mount once
    at import and then hard-codes ``/app`` somewhere downstream.
    """
    monkeypatch.setattr(credentials_router, "_CONNECT_RETURN_PATH", "/console/oauth/connected")
    complete = AsyncMock(return_value="cred_123")
    app = _build_app(complete_mock=complete)

    response = _get(app, {"state": "valid-state", "code": "the-code"})

    location = _location(response)
    parts = urlsplit(location)
    assert parts.path == "/console/oauth/connected", location
    assert parse_qs(parts.query).get("status") == ["ok"], location
