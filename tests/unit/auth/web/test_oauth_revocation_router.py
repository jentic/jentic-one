"""Unit tests for the dual-arm ``POST /oauth/revoke`` route (RFC 7009, G11).

Route-level concerns only (the service semantics are pinned in
``tests/unit/auth/test_oauth_revocation_service.py`` and the SQLite matrix in
``tests/integration/auth/test_oauth_rfc7009_revocation.py``): content-type
negotiation between the two arms, the ``server.mcp.oauth.enabled`` gate on the
form arm (plain 404, before rate limiting and parsing — the DCR-door posture),
the single 400 for a missing ``token``, the namespaced per-IP rate limit, and
the pre-G11 JSON+bearer contract staying byte-compatible (the CLI logout
path).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import (
    ProblemDetailException,
    Unauthorized,
    problem_detail_exception_handler,
)

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, OAuthRateLimitConfig, ServerConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.state.backend import MemoryStateBackend

_FORM = "application/x-www-form-urlencoded"


async def _verify_ok(token: str, request: object) -> Identity:
    return Identity(sub="usr_cli", actor_type=ActorType.USER, permissions=[])


async def _verify_reject(token: str, request: object) -> Identity:
    raise Unauthorized(detail="Invalid or expired token", type="unauthorized")


def _make_client(
    *,
    oauth_enabled: bool = True,
    rate_limit: OAuthRateLimitConfig | None = None,
    verify_token: object = _verify_ok,
) -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        oauth_rate_limit=rate_limit or OAuthRateLimitConfig(),
    )
    server = ServerConfig()
    server.mcp.oauth.enabled = oauth_enabled
    mock_ctx.config.server = server
    app.state.ctx = mock_ctx
    app.state.auth_state_backend = MemoryStateBackend()
    # The JSON arm's platform-bearer contract resolves through the same
    # verifier hook the real surface installs (auth/web/app.py).
    app.state.verify_token = verify_token
    return TestClient(app)


# --- the gate on the form arm (matches the DCR front door, §4.2) -------------


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_disabled_form_arm_is_plain_404(mock_svc_cls: MagicMock) -> None:
    """server.mcp.oauth.enabled=false → the framework's own route-not-found
    body on the RFC 7009 arm; the service never runs."""
    client = _make_client(oauth_enabled=False)
    resp = client.post("/oauth/revoke", data={"token": "at_x", "client_id": "oc_1"}, headers={})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    mock_svc_cls.return_value.revoke_client_token.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_disabled_form_arm_hides_rate_limit_and_validation(mock_svc_cls: MagicMock) -> None:
    """Neither a 429 nor a 400 may reveal the gate is on: malformed and
    over-quota form requests against a disabled arm all answer the same 404."""
    client = _make_client(
        oauth_enabled=False,
        rate_limit=OAuthRateLimitConfig(exchange_rpm=1, exchange_burst=1),
    )
    for _ in range(3):  # over the burst, and with no token field at all
        resp = client.post("/oauth/revoke", data={"junk": "1"})
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_disabled_gate_does_not_touch_the_json_arm(mock_token_svc_cls: MagicMock) -> None:
    """The legacy platform arm is NOT behind the MCP OAuth gate — `jentic
    logout` must keep working on deployments that never enable MCP OAuth."""
    mock_token_svc_cls.return_value.revoke = AsyncMock()
    client = _make_client(oauth_enabled=False)
    resp = client.post(
        "/oauth/revoke",
        json={"token": "at_mine"},
        headers={"Authorization": "Bearer at_valid"},
    )
    assert resp.status_code == 200
    mock_token_svc_cls.return_value.revoke.assert_awaited_once()


# --- the RFC 7009 form arm ----------------------------------------------------


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_form_arm_forwards_token_hint_and_client_id(mock_svc_cls: MagicMock) -> None:
    mock_svc_cls.return_value.revoke_client_token = AsyncMock()
    client = _make_client()

    resp = client.post(
        "/oauth/revoke",
        data={"token": "rt_x", "token_type_hint": "refresh_token", "client_id": "oc_1"},
    )

    assert resp.status_code == 200
    assert resp.content == b""
    mock_svc_cls.return_value.revoke_client_token.assert_awaited_once_with(
        "rt_x", client_id="oc_1", token_type_hint="refresh_token"
    )


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_form_arm_without_hint_or_client_id(mock_svc_cls: MagicMock) -> None:
    """hint and client_id are optional on the wire; empty client_id normalises
    to None (it can then never lineage-match — a safe no-op)."""
    mock_svc_cls.return_value.revoke_client_token = AsyncMock()
    client = _make_client()

    resp = client.post("/oauth/revoke", data={"token": "at_x"})

    assert resp.status_code == 200
    mock_svc_cls.return_value.revoke_client_token.assert_awaited_once_with(
        "at_x", client_id=None, token_type_hint=None
    )


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_form_arm_missing_token_is_400(mock_svc_cls: MagicMock) -> None:
    """The endpoint's only 400 (RFC 7009 §2.2.1 invalid_request)."""
    client = _make_client()
    resp = client.post("/oauth/revoke", data={"token_type_hint": "access_token"})
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_request"
    mock_svc_cls.return_value.revoke_client_token.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_form_arm_needs_no_authorization_header(mock_svc_cls: MagicMock) -> None:
    """Public clients authenticate by client_id lineage binding — the form arm
    must never demand a platform bearer (that was G11's original blocker)."""
    mock_svc_cls.return_value.revoke_client_token = AsyncMock()
    client = _make_client(verify_token=_verify_reject)

    resp = client.post("/oauth/revoke", data={"token": "at_x", "client_id": "oc_1"})

    assert resp.status_code == 200


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_form_arm_rate_limit_answers_429_with_retry_after(mock_svc_cls: MagicMock) -> None:
    """Own namespaced per-IP bucket (`oauth-revocation`), 3a-2 fix-wave
    pattern: over-quota answers 429 + Retry-After."""
    mock_svc_cls.return_value.revoke_client_token = AsyncMock()
    client = _make_client(rate_limit=OAuthRateLimitConfig(exchange_rpm=1, exchange_burst=1))

    first = client.post("/oauth/revoke", data={"token": "at_x", "client_id": "oc_1"})
    second = client.post("/oauth/revoke", data={"token": "at_x", "client_id": "oc_1"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    mock_svc_cls.return_value.revoke_client_token.assert_awaited_once()


# --- the legacy JSON+bearer arm (pre-G11 contract, unchanged) -----------------


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_revokes_with_platform_identity(mock_token_svc_cls: MagicMock) -> None:
    """The CLI-logout shape: JSON body + bearer → TokenService.revoke with the
    resolved identity (owner-scoped revocation)."""
    mock_token_svc_cls.return_value.revoke = AsyncMock()
    client = _make_client()

    resp = client.post(
        "/oauth/revoke",
        json={"token": "at_mine"},
        headers={"Authorization": "Bearer at_valid"},
    )

    assert resp.status_code == 200
    args = mock_token_svc_cls.return_value.revoke.await_args
    assert args.args == ("at_mine",)
    assert args.kwargs["identity"].sub == "usr_cli"


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_still_requires_bearer(mock_token_svc_cls: MagicMock) -> None:
    """A JSON request with a bad/missing bearer keeps answering 401 — the form
    encoding, not the auth outcome, selects the arm."""
    client = _make_client(verify_token=_verify_reject)

    resp = client.post("/oauth/revoke", json={"token": "at_mine"})

    assert resp.status_code == 401
    mock_token_svc_cls.return_value.revoke.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_invalid_body_is_422(mock_token_svc_cls: MagicMock) -> None:
    """Schema rejections keep FastAPI's 422 shape (pre-G11 behaviour)."""
    client = _make_client()

    resp = client.post(
        "/oauth/revoke",
        json={"not_token": "x"},
        headers={"Authorization": "Bearer at_valid"},
    )

    assert resp.status_code == 422
    mock_token_svc_cls.return_value.revoke.assert_not_called()
