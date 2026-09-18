"""Unit tests for the integrations router — HTTP contract, not state.

Pins the router's four endpoints (``POST /integrations:connect``,
``GET /connect-sessions/{id}``, ``POST /connect-sessions/{id}:confirm``,
``GET /connect-sessions/{id}/status``) to the response shapes,
discriminated-union serialisation, and error → HTTP status mapping the
UI + agents rely on. State-machine behaviour is covered in the integration
test at ``tests/integration/control/test_connect_session_service.py``;
this file mocks ``ConnectSessionService`` at the boundary and exercises
only the router's own logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
    ConnectSessionService,
    CreatedSession,
    DeviceAuthorizationConfirmResult,
    ReviewData,
    ScopeView,
    StatusResult,
)
from jentic_one.control.services.integrations.device_authorization import (
    DeviceAuthorizationUpstreamError,
)
from jentic_one.control.services.integrations.errors import (
    AgentNotFoundError,
    ConfirmationForbiddenError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    ScopeValidationError,
)
from jentic_one.control.services.vendors.service import UnknownVendorError
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.deps import get_connect_session_service
from jentic_one.control.web.routers import integrations as integrations_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state import MemoryStateBackend
from jentic_one.shared.web import deps as shared_deps

_USER_IDENTITY = Identity(sub="usr_alice", permissions=["credentials:write"])
_AGENT_IDENTITY = Identity(
    sub="agnt_scout",
    permissions=["credentials:connect", "credentials:write"],
    actor_type=ActorType.AGENT,
)


def _build_app(*, svc: Any, identity: Identity = _USER_IDENTITY) -> FastAPI:
    """Build a minimal FastAPI app with only the integrations router.

    The service is passed in as an AsyncMock spec'd on ConnectSessionService
    so tests can assert on the exact args the router forwarded. Identity
    is stubbed via ``resolve_identity`` — that dependency is the single
    identity source ``get_current_identity`` delegates through.
    """
    app = FastAPI()
    app.include_router(integrations_router.router)
    # Error → problem-details mapping lives in app-level exception handlers
    # (control/web/errors.py), so the router contract can only be exercised
    # with them registered — same wiring as ``create_app``.
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[get_connect_session_service] = lambda: svc
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: identity
    return app


# ---------------------------------------------------------------------------
# POST /integrations:connect
# ---------------------------------------------------------------------------


def test_connect_uses_agent_identity_when_caller_is_agent() -> None:
    # Agent callers get their own identity injected — the payload
    # ``agent_id`` is refused outright (see the dedicated test below).
    # This is the load-bearing anti-spoofing property of the endpoint.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.create_session = AsyncMock(
        return_value=CreatedSession(
            session_id="sess_1",
            approval_url="https://example.com/app/credentials?approve=sess_1&poll_token=tok",
            poll_token="tok",
            resolved_flow="device_authorization",
        )
    )
    app = _build_app(svc=svc, identity=_AGENT_IDENTITY)
    with TestClient(app) as client:
        resp = client.post("/integrations:connect", json={"vendor": "gh"})
    assert resp.status_code == 201
    call = svc.create_session.await_args
    assert call is not None
    assert call.kwargs["agent_id"] == "agnt_scout"
    assert call.kwargs["initiator_actor_id"] == "agnt_scout"


def test_connect_refuses_agent_caller_passing_agent_id() -> None:
    # Agent callers are refused if they pass ``agent_id`` in the payload —
    # the caller *is* the agent, so binding to a different id is a
    # permission-boundary violation. This holds even when the passed id
    # matches the caller's own identity (belt + braces: the caller has
    # no legitimate reason to send it).
    svc = AsyncMock(spec=ConnectSessionService)
    app = _build_app(svc=svc, identity=_AGENT_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/integrations:connect",
            json={"vendor": "gh", "agent_id": "agnt_scout"},
        )
    assert resp.status_code == 403
    svc.create_session.assert_not_called()


def test_connect_allows_user_caller_without_agent_id() -> None:
    # ``agent_id`` is optional today — credentials still bind through
    # toolkits, so the eventual permission grant is a no-op when no
    # agent is named. Will become mandatory once agent-credential
    # bindings replace toolkit membership.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.create_session = AsyncMock(
        return_value=CreatedSession(
            session_id="sess_1",
            approval_url="https://example.com/app/credentials?approve=sess_1&poll_token=tok",
            poll_token="tok",
            resolved_flow="device_authorization",
        )
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post("/integrations:connect", json={"vendor": "gh"})
    assert resp.status_code == 201
    call = svc.create_session.await_args
    assert call is not None
    assert call.kwargs["agent_id"] is None
    assert call.kwargs["initiator_actor_id"] == "usr_alice"


def test_connect_maps_unknown_vendor_to_404() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.create_session = AsyncMock(side_effect=UnknownVendorError("nope"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post("/integrations:connect", json={"vendor": "nope"})
    assert resp.status_code == 404


def test_connect_returns_session_id_and_poll_token() -> None:
    # The UI + agents both depend on this response shape — session_id
    # is the URL pivot for confirm/status, and poll_token is the
    # capability that gates the status endpoint.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.create_session = AsyncMock(
        return_value=CreatedSession(
            session_id="sess_9",
            approval_url="https://example.com/app/credentials?approve=sess_9&poll_token=tok9",
            poll_token="tok9",
            resolved_flow="authorization_code",
        )
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/integrations:connect", json={"vendor": "auth-vendor", "agent_id": "agnt_1"}
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body == {
        "session_id": "sess_9",
        "approval_url": "https://example.com/app/credentials?approve=sess_9&poll_token=tok9",
        "poll_token": "tok9",
        "resolved_flow": "authorization_code",
    }


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}
# ---------------------------------------------------------------------------


def test_get_review_data_returns_scope_catalog() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.get_review_data = AsyncMock(
        return_value=ReviewData(
            session_id="sess_1",
            state="created",
            vendor_key="gh",
            vendor_display_name="GitHub",
            resolved_flow="device_authorization",
            reason=None,
            requested_by_actor_id="agnt_1",
            scopes=[
                ScopeView(
                    name="repo",
                    classification="write",
                    default=False,
                    requested=True,
                    description="Full control of private repositories",
                )
            ],
            requested_permission_rules=[],
            api_vendor="gh",
            api_name="gh/api.gh",
            api_version=None,
        )
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_1", params={"poll_token": "tok"})
    assert resp.status_code == 200
    call = svc.get_review_data.await_args
    assert call is not None
    assert call.kwargs["poll_token"] == "tok"
    body = resp.json()
    assert body["vendor_display_name"] == "GitHub"
    assert body["scopes"][0] == {
        "name": "repo",
        "classification": "write",
        "default": False,
        "requested": True,
        "description": "Full control of private repositories",
    }


def test_get_review_data_requires_poll_token() -> None:
    # The review payload is poll_token-gated — a bare GET (no token) must
    # fail schema validation before the service is ever consulted.
    svc = AsyncMock(spec=ConnectSessionService)
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_1")
    assert resp.status_code == 422
    svc.get_review_data.assert_not_called()


def test_get_review_data_maps_invalid_poll_token_uniformly_to_403() -> None:
    # Missing session and token mismatch both surface as
    # ``InvalidPollTokenError`` → 403 (no session-id enumeration oracle;
    # session ids ride approval URLs, so they are not secrets).
    svc = AsyncMock(spec=ConnectSessionService)
    svc.get_review_data = AsyncMock(side_effect=InvalidPollTokenError("invalid poll_token"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_missing", params={"poll_token": "t"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:confirm — discriminated response shapes
# ---------------------------------------------------------------------------


def test_confirm_device_authorization_serialises_user_code_response() -> None:
    # The discriminated response by ``kind`` is the UI's routing hook:
    # a device_code response drives the AwaitingStep with the user_code
    # panel; an authorization_code response drives the redirect panel.
    # Cross-wiring the two ships broken UX to the human.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(
        return_value=DeviceAuthorizationConfirmResult(
            user_code="ABCD-1234",
            verification_uri="https://idp.example.com/device",
            verification_uri_complete=None,
            poll_interval_seconds=5,
        )
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": ["repo"], "permission_rules": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "device_authorization"
    assert body["user_code"] == "ABCD-1234"
    assert body["poll_interval_seconds"] == 5


def test_confirm_authorization_code_serialises_authorize_url_response() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(
        return_value=AuthCodeConfirmResult(authorize_url="https://idp.example.com/authorize?...")
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": ["scope-a"], "permission_rules": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "authorization_code"
    assert body["authorize_url"].startswith("https://idp.example.com/authorize")


def test_confirm_maps_self_confirm_to_403() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(side_effect=ConfirmationForbiddenError("agents cannot self-confirm"))
    app = _build_app(svc=svc, identity=_AGENT_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": [], "permission_rules": []},
        )
    assert resp.status_code == 403


def test_confirm_maps_invalid_state_to_409() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(side_effect=InvalidStateTransitionError("sess_1", "polling", "confirm"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": [], "permission_rules": []},
        )
    assert resp.status_code == 409


def test_confirm_maps_scope_validation_to_400_with_unknown_scopes() -> None:
    # Unknown scopes surface the offending list so the UI can highlight
    # exactly which checkbox to clear — a generic 400 with a plain string
    # would push that debugging onto the human.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(side_effect=ScopeValidationError(["bogus_scope", "other_bogus"]))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={
                "confirmed_scopes": ["bogus_scope", "other_bogus"],
                "permission_rules": [],
            },
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["unknown_scopes"] == ["bogus_scope", "other_bogus"]


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}/status
# ---------------------------------------------------------------------------


def test_status_returns_pending() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.get_status = AsyncMock(return_value=StatusResult(status="pending"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_1/status", params={"poll_token": "tok"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["credential_id"] is None


def test_status_returns_connected_with_bound_scopes() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.get_status = AsyncMock(
        return_value=StatusResult(
            status="connected",
            connected_as="alice",
            credential_id="cred_1",
            bound_scopes=["repo", "read:user"],
        )
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_9/status", params={"poll_token": "tok9"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "connected"
    assert body["connected_as"] == "alice"
    assert body["bound_scopes"] == ["repo", "read:user"]


def test_status_maps_invalid_poll_token_to_403() -> None:
    # The poll_token is the *only* capability check on this endpoint —
    # a leaky mapping (e.g. 404) would let a caller enumerate session
    # ids by observing status-code differences.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.get_status = AsyncMock(side_effect=InvalidPollTokenError("mismatch"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_1/status", params={"poll_token": "wrong"})
    assert resp.status_code == 403


def test_status_maps_invalid_poll_token_uniformly_to_403() -> None:
    # ``get_status`` is designed to raise ``InvalidPollTokenError`` for both
    # "session missing" and "poll_token mismatch" (the enumeration-oracle
    # guard). Router must map it to 403 — a 404 branch here would silently
    # reintroduce the leak.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.get_status = AsyncMock(side_effect=InvalidPollTokenError("mismatch"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/connect-sessions/sess_x/status", params={"poll_token": "t"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Per-actor rate limit on POST /integrations:connect
# ---------------------------------------------------------------------------


def test_connect_rate_limit_returns_429_with_retry_after() -> None:
    # The router builds a token-bucket limiter lazily; a burst of
    # requests from the same actor must land a 429 before the burst is
    # exhausted by any legitimate load. Set the rpm/burst to tight
    # values for the duration of this test so we can trip the limiter
    # in a handful of calls without shipping unrealistic knobs.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.create_session = AsyncMock(
        return_value=CreatedSession(
            session_id="sess_1",
            approval_url="https://example.com/app",
            poll_token="tok",
            resolved_flow="device_authorization",
        )
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)

    # Tighten the caps so the burst is 2, then the third call must 429.
    app.state.integrations_connect_limiter = RateLimiter(
        MemoryStateBackend(),
        default_rpm=1,
        burst=2,
        namespace="test_integrations_connect",
    )

    with TestClient(app) as client:
        assert client.post("/integrations:connect", json={"vendor": "gh"}).status_code == 201
        assert client.post("/integrations:connect", json={"vendor": "gh"}).status_code == 201
        third = client.post("/integrations:connect", json={"vendor": "gh"})
        assert third.status_code == 429
        assert "Retry-After" in third.headers
        assert int(third.headers["Retry-After"]) >= 1


def test_confirm_requires_poll_token_and_forwards_it_with_identity() -> None:
    # ``:confirm`` is poll_token-gated like the review read — without the
    # capability, any ``credentials:write`` holder could confirm any
    # session. The router must also hand the full identity to the service
    # (agent-ownership validation happens there).
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(
        return_value=AuthCodeConfirmResult(authorize_url="https://idp.example.com/authorize")
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        bare = client.post(
            "/connect-sessions/sess_1:confirm",
            json={"confirmed_scopes": [], "permission_rules": []},
        )
        assert bare.status_code == 422
        svc.confirm.assert_not_called()

        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": [], "permission_rules": []},
        )
    assert resp.status_code == 200
    call = svc.confirm.await_args
    assert call is not None
    assert call.kwargs["poll_token"] == "tok"
    assert call.kwargs["identity"] is _USER_IDENTITY


def test_confirm_maps_agent_not_found_to_400() -> None:
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(side_effect=AgentNotFoundError("agnt_ghost"))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": [], "permission_rules": [], "agent_id": "agnt_ghost"},
        )
    assert resp.status_code == 400


def test_confirm_maps_vendor_upstream_error_to_502_retryable() -> None:
    # A vendor-side ``begin`` failure is not a server fault and not
    # permanent (the service rolls the session back to ``created``): the
    # human must see a retryable 502 problem detail, never a raw 500.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(side_effect=DeviceAuthorizationUpstreamError(404))
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/connect-sessions/sess_1:confirm",
            params={"poll_token": "tok"},
            json={"confirmed_scopes": [], "permission_rules": []},
        )
    assert resp.status_code == 502
    body = resp.json()
    assert body["retryable"] is True


def test_confirm_shares_connect_rate_limit_bucket() -> None:
    # ``:confirm`` is the endpoint that actually calls the vendor (device
    # ``begin``), and a failed begin leaves the session retryable — during
    # a vendor incident it must not be free to hammer. Same per-actor
    # bucket as ``:connect``.
    svc = AsyncMock(spec=ConnectSessionService)
    svc.confirm = AsyncMock(
        return_value=AuthCodeConfirmResult(authorize_url="https://idp.example.com/authorize")
    )
    app = _build_app(svc=svc, identity=_USER_IDENTITY)
    app.state.integrations_connect_limiter = RateLimiter(
        MemoryStateBackend(),
        default_rpm=1,
        burst=2,
        namespace="test_integrations_confirm",
    )
    with TestClient(app) as client:
        payload = {"confirmed_scopes": [], "permission_rules": []}
        params = {"poll_token": "tok"}
        first = client.post("/connect-sessions/sess_1:confirm", params=params, json=payload)
        second = client.post("/connect-sessions/sess_1:confirm", params=params, json=payload)
        third = client.post("/connect-sessions/sess_1:confirm", params=params, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "Retry-After" in third.headers
