"""Unit tests for the dual-arm ``POST /oauth/revoke`` route (RFC 7009, G11).

Route-level concerns only (the service semantics are pinned in
``tests/unit/auth/test_oauth_revocation_service.py`` and the SQLite matrix in
``tests/integration/auth/test_oauth_rfc7009_revocation.py``): content-type
negotiation between the two arms (owned by ``_RevocationRoute``), the
``server.mcp.oauth.enabled`` gate on the form arm (plain 404, before the size
cap, rate limiting, and parsing — the DCR-door posture), the form arm's
RFC 6749 §5.2 error dialect (400/413/429 — review F2/F3), the namespaced
per-IP rate limit, and the pre-G11 JSON+bearer contract staying byte-identical
including every 422 body and the 422/401 precedence (review F1 — pinned
against a live replica of the pre-G11 route, the review's probe ported).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import APIRouter, FastAPI, Response
from fastapi.testclient import TestClient
from jentic.problem_details import (
    ProblemDetailException,
    Unauthorized,
    problem_detail_exception_handler,
)

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.auth.web.schemas.oauth import RevokeRequest
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, OAuthRateLimitConfig, ServerConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.web import get_current_identity

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
def test_form_arm_missing_token_is_400_rfc6749_dialect(mock_svc_cls: MagicMock) -> None:
    """The endpoint's only 400 — pinned to the RFC 6749 §5.2 error dialect
    (RFC 7009 §2.2.1: top-level ``error`` member), NOT platform Problem
    Details (review F2; the DCR door's reshaping precedent)."""
    client = _make_client()
    resp = client.post("/oauth/revoke", data={"token_type_hint": "access_token"})
    assert resp.status_code == 400
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "token is required",
    }
    mock_svc_cls.return_value.revoke_client_token.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.OAuthRevocationService")
def test_form_arm_oversized_body_is_413(mock_svc_cls: MagicMock) -> None:
    """Declared-length bodies beyond the 64 KiB raw cap are refused before
    parsing (the DCR door's belt, review F3) — same §5.2 dialect."""
    client = _make_client()
    resp = client.post(
        "/oauth/revoke",
        content=b"token=" + b"a" * (65 * 1024),
        headers={"Content-Type": _FORM},
    )
    assert resp.status_code == 413
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "request body too large",
    }
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
    pattern: over-quota answers 429 + Retry-After — body pinned to the
    RFC 6749 §5.2 dialect (`slow_down`, RFC 8628 §3.5), not Problem Details
    (review F2)."""
    mock_svc_cls.return_value.revoke_client_token = AsyncMock()
    client = _make_client(rate_limit=OAuthRateLimitConfig(exchange_rpm=1, exchange_burst=1))

    first = client.post("/oauth/revoke", data={"token": "at_x", "client_id": "oc_1"})
    second = client.post("/oauth/revoke", data={"token": "at_x", "client_id": "oc_1"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json() == {
        "error": "slow_down",
        "error_description": "rate limit exceeded, retry later",
    }
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
def test_json_arm_missing_token_422_body_pinned(mock_token_svc_cls: MagicMock) -> None:
    """Schema rejections keep FastAPI's NATIVE 422 body byte-for-byte (review
    F1): the ``["body", …]`` loc prefix, no pydantic ``url`` key. Pinned as a
    full-body equality so any drift in the framework-produced shape fails
    loudly instead of slipping through on a status-only assert."""
    client = _make_client()

    resp = client.post(
        "/oauth/revoke",
        json={"not_token": "x"},
        headers={"Authorization": "Bearer at_valid"},
    )

    assert resp.status_code == 422
    assert resp.json() == {
        "detail": [
            {
                "type": "missing",
                "loc": ["body", "token"],
                "msg": "Field required",
                "input": {"not_token": "x"},
            }
        ]
    }
    mock_token_svc_cls.return_value.revoke.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_malformed_json_422_body_pinned(mock_token_svc_cls: MagicMock) -> None:
    """Syntactically invalid JSON keeps the framework's ``json_invalid`` shape
    (``loc: ["body", pos]``, ``msg: "JSON decode error"``, ``input: {}``) —
    the exact pre-G11 bytes (review F1)."""
    client = _make_client()

    resp = client.post(
        "/oauth/revoke",
        content=b"{nope",
        headers={"Authorization": "Bearer at_valid", "Content-Type": "application/json"},
    )

    assert resp.status_code == 422
    assert resp.json() == {
        "detail": [
            {
                "type": "json_invalid",
                "loc": ["body", 1],
                "msg": "JSON decode error",
                "input": {},
                "ctx": {"error": "Expecting property name enclosed in double quotes"},
            }
        ]
    }
    mock_token_svc_cls.return_value.revoke.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_empty_body_422_body_pinned(mock_token_svc_cls: MagicMock) -> None:
    """An empty JSON body keeps the framework's missing-body shape."""
    client = _make_client()

    resp = client.post(
        "/oauth/revoke",
        content=b"",
        headers={"Authorization": "Bearer at_valid", "Content-Type": "application/json"},
    )

    assert resp.status_code == 422
    assert resp.json() == {
        "detail": [{"type": "missing", "loc": ["body"], "msg": "Field required", "input": None}]
    }
    mock_token_svc_cls.return_value.revoke.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_syntax_error_beats_bad_bearer(mock_token_svc_cls: MagicMock) -> None:
    """Pre-G11 precedence, restored (review F1): FastAPI parses the raw JSON
    body BEFORE dependencies, so syntactically invalid JSON answers 422 even
    alongside a missing/bad bearer — never 401."""
    client = _make_client(verify_token=_verify_reject)

    resp = client.post(
        "/oauth/revoke", content=b"{nope", headers={"Content-Type": "application/json"}
    )

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["type"] == "json_invalid"
    mock_token_svc_cls.return_value.revoke.assert_not_called()


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_schema_error_with_bad_bearer_is_401(mock_token_svc_cls: MagicMock) -> None:
    """…but a SCHEMA error (well-formed JSON, missing field) on a bad bearer
    answers 401: dependencies run before field validation (pre-G11 order)."""
    client = _make_client(verify_token=_verify_reject)

    resp = client.post("/oauth/revoke", json={"not_token": "x"})

    assert resp.status_code == 401
    mock_token_svc_cls.return_value.revoke.assert_not_called()


# --- pre-G11 equivalence probe (the review's probe, ported) -------------------
#
# The adversarial review (F1) diffed the pre-G11 route against the G11 route
# across a request-shape matrix and found the hand-parsed JSON arm drifting on
# every 422. This ports that probe: a byte-for-byte replica of the pre-G11
# endpoint (its exact signature — native body validation + bearer dependency)
# runs beside the shipped route, and every JSON-arm shape must produce the
# same status AND the same body bytes under both gate states. Form-encoded
# shapes are deliberately out of matrix: they now select the RFC 7009 arm
# (review F7 — accepted liberalization; pre-G11 they were 422 rejects).


def _make_pre_g11_client(*, verify_token: object = _verify_ok) -> TestClient:
    """A replica of the pre-G11 ``POST /oauth/revoke`` (native FastAPI route)."""
    legacy_router = APIRouter()

    @legacy_router.post("/oauth/revoke", status_code=200)
    async def legacy_revoke_endpoint(
        body: RevokeRequest,
        identity: Identity = get_current_identity(allow_expired_password=True),  # noqa: B008
    ) -> Response:
        return Response(status_code=200)

    app = FastAPI()
    app.include_router(legacy_router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.state.verify_token = verify_token
    return TestClient(app)


_JSON_ARM_PROBE_MATRIX: list[tuple[str, dict[str, object] | None, bytes | None, dict[str, str]]] = [
    ("valid body", {"token": "at_x"}, None, {}),
    ("valid body + hint", {"token": "rt_x", "token_type_hint": "refresh_token"}, None, {}),
    ("missing token field", {"not_token": "x"}, None, {}),
    ("token wrong type", {"token": 5}, None, {}),
    ("null token", {"token": None}, None, {}),
    ("malformed json", None, b"{nope", {"Content-Type": "application/json"}),
    ("empty body", None, b"", {"Content-Type": "application/json"}),
    ("json array body", None, b"[1, 2]", {"Content-Type": "application/json"}),
    ("text/plain json bytes", None, b'{"token": "at_x"}', {"Content-Type": "text/plain"}),
    ("no content-type json bytes", None, b'{"token": "at_x"}', {}),
]


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_json_arm_matches_pre_g11_route_byte_for_byte(mock_token_svc_cls: MagicMock) -> None:
    """Every JSON-arm shape x {good, bad bearer} x {gate on, off} answers the
    exact status and body bytes the pre-G11 native route produced (review F1)."""
    mock_token_svc_cls.return_value.revoke = AsyncMock()

    for gate in (True, False):
        for bearer_name, verify in (("good bearer", _verify_ok), ("bad bearer", _verify_reject)):
            legacy = _make_pre_g11_client(verify_token=verify)
            current = _make_client(oauth_enabled=gate, verify_token=verify)
            for shape_name, json_body, content, extra_headers in _JSON_ARM_PROBE_MATRIX:
                headers = dict(extra_headers)
                if bearer_name == "good bearer":
                    headers["Authorization"] = "Bearer at_valid"
                if json_body is not None:
                    old = legacy.post("/oauth/revoke", json=json_body, headers=headers)
                    new = current.post("/oauth/revoke", json=json_body, headers=headers)
                else:
                    old = legacy.post("/oauth/revoke", content=content, headers=headers)
                    new = current.post("/oauth/revoke", content=content, headers=headers)
                label = f"{shape_name} / {bearer_name} / gate={'on' if gate else 'off'}"
                assert new.status_code == old.status_code, label
                assert new.content == old.content, label
