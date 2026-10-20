"""Unit tests for the broker's CLI-facade auth dependency and its cached validator.

The dependency reads the invocation token from whichever ``Authorization``
scheme a vendor CLI sends, accepts only ``cit_`` tokens, and checks the
token's vendor against the facade's. The resolver is an in-memory fake; the
real admin-DB resolution is covered in
``tests/integration/broker/test_cli_invocation_auth.py``.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.broker.core.token_validation import CachedCliInvocationValidator
from jentic_one.broker.web.deps import require_cli_facade_invocation
from jentic_one.shared.auth.cli_invocation import CliInvocationClaims, CliInvocationIdentity
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

_TOKEN = "cit_unit-test-token"  # pragma: allowlist secret


def _resolution(
    *, vendor: str = "github", active: bool = True, expires_in: timedelta = timedelta(minutes=5)
) -> CliInvocationIdentity:
    now = datetime.now(UTC)
    return CliInvocationIdentity(
        identity=Identity(
            sub="agnt_cli_unit",
            actor_type=ActorType.AGENT,
            permissions=["capabilities:execute"],
            expires_at=now + expires_in,
            active=active,
        ),
        claims=CliInvocationClaims(
            invocation_id="cli_unit",
            vendor=vendor,
            trace_id="0af7651916cd43dd8448eb211c80319c",
            command_title="pr create",
            expires_at=now + expires_in,
            hard_expires_at=now + timedelta(hours=1),
        ),
    )


class _FakeResolver:
    """Answers one known token; counts lookups so cache behaviour is observable."""

    def __init__(self, resolution: CliInvocationIdentity | None) -> None:
        self.resolution = resolution
        self.calls: list[str] = []

    async def resolve_cli_invocation_token(self, token: str) -> CliInvocationIdentity | None:
        self.calls.append(token)
        return self.resolution if token == _TOKEN else None


def _client(resolver: _FakeResolver, *, vendor: str = "github") -> TestClient:
    router = APIRouter()
    require_invocation = require_cli_facade_invocation(vendor)

    @router.get("/facade")
    async def facade(
        invocation: CliInvocationIdentity = require_invocation,
    ) -> dict[str, str]:
        return {"sub": invocation.identity.sub, "trace_id": invocation.claims.trace_id}

    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.include_router(router)
    app.state.broker_cli_invocation_validator = CachedCliInvocationValidator(
        resolver=resolver, cache_ttl_seconds=60.0
    )
    return TestClient(app, raise_server_exceptions=False)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize(
    "authorization",
    [
        f"token {_TOKEN}",  # gh
        f"Bearer {_TOKEN}",  # cf
        f"bearer {_TOKEN}",
        _basic("x-access-token", _TOKEN),  # git
    ],
)
def test_every_cli_auth_scheme_is_accepted(authorization: str) -> None:
    resp = _client(_FakeResolver(_resolution())).get(
        "/facade", headers={"Authorization": authorization}
    )
    assert resp.status_code == 200
    assert resp.json() == {"sub": "agnt_cli_unit", "trace_id": "0af7651916cd43dd8448eb211c80319c"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer at_agent-access-token"},  # an access token is refused
        {"Authorization": "Bearer jak_agent-api-key"},
        {"Authorization": "Digest cit_unit-test-token"},
        {"Authorization": "Basic not-base64!"},
        {"Authorization": _basic("x-access-token", "")},
        {"Authorization": "token "},
        {"x-jentic-api-key": _TOKEN},  # never read by the facade dependency
    ],
)
def test_anything_but_a_cit_token_is_refused_without_a_lookup(headers: dict[str, str]) -> None:
    resolver = _FakeResolver(_resolution())
    resp = _client(resolver).get("/facade", headers=headers)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resolver.calls == []


def test_token_minted_for_another_vendor_is_refused() -> None:
    resolver = _FakeResolver(_resolution(vendor="cloudflare"))
    resp = _client(resolver, vendor="github").get(
        "/facade", headers={"Authorization": f"token {_TOKEN}"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired CLI invocation token"


@pytest.mark.parametrize(
    "resolution",
    [
        None,
        _resolution(active=False),
        _resolution(expires_in=timedelta(seconds=-1)),
    ],
)
def test_unknown_inactive_or_expired_tokens_are_refused(
    resolution: CliInvocationIdentity | None,
) -> None:
    resp = _client(_FakeResolver(resolution)).get(
        "/facade", headers={"Authorization": f"token {_TOKEN}"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired CLI invocation token"


async def test_validator_caches_verdicts_within_ttl() -> None:
    resolver = _FakeResolver(_resolution())
    validator = CachedCliInvocationValidator(resolver=resolver, cache_ttl_seconds=60.0)
    await validator.validate(_TOKEN)
    await validator.validate(_TOKEN)
    assert resolver.calls == [_TOKEN]
    with pytest.raises(TokenValidationError):
        await validator.validate("cit_unknown")
    with pytest.raises(TokenValidationError):
        await validator.validate("cit_unknown")
    assert resolver.calls == [_TOKEN, "cit_unknown"]


async def test_validator_with_zero_ttl_resolves_every_time() -> None:
    resolver = _FakeResolver(_resolution())
    validator = CachedCliInvocationValidator(resolver=resolver, cache_ttl_seconds=0)
    await validator.validate(_TOKEN)
    resolver.resolution = _resolution(active=False)
    with pytest.raises(TokenValidationError, match="token_inactive"):
        await validator.validate(_TOKEN)


async def test_validator_evicts_past_max_entries() -> None:
    resolver = _FakeResolver(_resolution())
    validator = CachedCliInvocationValidator(resolver=resolver, max_entries=1)
    with pytest.raises(TokenValidationError):
        await validator.validate("cit_a")
    await validator.validate(_TOKEN)
    with pytest.raises(TokenValidationError):
        await validator.validate("cit_a")
    assert resolver.calls == ["cit_a", _TOKEN, "cit_a"]


def test_validator_rejects_non_positive_max_entries() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        CachedCliInvocationValidator(resolver=_FakeResolver(None), max_entries=0)
