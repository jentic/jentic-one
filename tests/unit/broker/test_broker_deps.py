"""Unit tests for broker web deps — token validation + execute-scope enforcement.

Toolkit *binding* enforcement moved out of ``deps.py`` into ``select_toolkit``
(handler-side, after discovery) in §03 — see ``test_toolkit_select.py``. These
tests cover only what the dependency still owns: authenticate + require the
execute scope.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import DualTokenValidator, JwtTokenValidator, JwtVerifier
from jentic_one.broker.web.deps import RequireToolkitAccess
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

_JWT_SECRET = "broker-deps-test-secret-32-bytes-long!!"  # pragma: allowlist secret


def _make_identity(
    *,
    sub: str = "agnt_test1",
    actor_type: ActorType = ActorType.AGENT,
    permissions: list[str] | None = None,
    active: bool = True,
) -> Identity:
    return Identity(
        sub=sub,
        actor_type=actor_type,
        permissions=permissions or [BROKER_EXECUTE_SCOPE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=active,
    )


_SENTINEL = object()


def _create_test_app(resolver_return: Identity | None | object = _SENTINEL) -> TestClient:
    """Build a test client with a mocked opaque-token resolver behind the dual validator."""
    router = APIRouter()

    @router.post("/execute")
    async def execute(request: Request, _identity: RequireToolkitAccess) -> Response:
        return Response(content="ok", status_code=200)

    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.include_router(router)

    effective_return = _make_identity() if resolver_return is _SENTINEL else resolver_return
    mock_resolver = AsyncMock()
    mock_resolver.resolve_access_token = AsyncMock(return_value=effective_return)
    opaque = CachedTokenValidator(resolver=mock_resolver, cache_ttl_seconds=60.0)
    jwt_validator = JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET))
    app.state.broker_token_validator = DualTokenValidator(opaque=opaque, jwt=jwt_validator)

    return TestClient(app, raise_server_exceptions=False)


def test_returns_200_with_valid_token_and_scope() -> None:
    client = _create_test_app()
    resp = client.post("/execute", headers={"Authorization": "Bearer at_valid"})
    assert resp.status_code == 200


def test_returns_401_without_authorization_header() -> None:
    client = _create_test_app()
    resp = client.post("/execute")
    assert resp.status_code == 401


def test_returns_401_with_invalid_token() -> None:
    client = _create_test_app(resolver_return=None)
    resp = client.post("/execute", headers={"Authorization": "Bearer at_invalid"})
    assert resp.status_code == 401


def test_returns_401_with_inactive_token() -> None:
    client = _create_test_app(resolver_return=_make_identity(active=False))
    resp = client.post("/execute", headers={"Authorization": "Bearer at_revoked"})
    assert resp.status_code == 401


def test_returns_403_with_insufficient_scope() -> None:
    client = _create_test_app(resolver_return=_make_identity(permissions=["read:only"]))
    resp = client.post("/execute", headers={"Authorization": "Bearer at_limited"})
    assert resp.status_code == 403
    assert resp.json()["type"] == "insufficient_scope"


def test_malformed_jwt_shaped_token_returns_401_not_500() -> None:
    """A JWT-shaped but undecodable token must not escape as a 500 (#880 review, finding #1)."""
    client = _create_test_app()
    resp = client.post("/execute", headers={"Authorization": "Bearer aaa.bbb.ccc"})
    assert resp.status_code == 401


def test_jwt_with_out_of_range_exp_returns_401_not_500() -> None:
    """A validly-signed token with an absurd ``exp`` must be a 401, not a 500 (finding #2)."""
    token = jwt.encode(
        {"sub": "x", "exp": 10**20, "actor_type": "agent"},
        _JWT_SECRET,
        algorithm="HS256",
    )
    client = _create_test_app()
    resp = client.post("/execute", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_jwt_with_non_finite_exp_returns_401_not_500() -> None:
    """A signed ``exp: inf`` (OverflowError inside PyJWT decode) must be a 401, not a 500."""
    token = jwt.encode(
        {"sub": "x", "exp": float("inf"), "actor_type": "agent"},
        _JWT_SECRET,
        algorithm="HS256",
    )
    client = _create_test_app()
    resp = client.post("/execute", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
