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
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.broker.core.token_validation import CachedCliInvocationValidator
from jentic_one.broker.web.deps import require_cli_facade_invocation, require_cli_proxy_invocation
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.shared.auth.cli_invocation import CliInvocationClaims, CliInvocationIdentity
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import CliFacadesConfig
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


# ── the proxy route's dependency ─────────────────────────────────────────────


def _proxy_client(resolver: _FakeResolver, facades: CliFacadesConfig) -> TestClient:
    router = APIRouter()

    @router.get("/proxy")
    async def proxy(
        invocation: Annotated[CliInvocationIdentity, Depends(require_cli_proxy_invocation)],
    ) -> dict[str, str]:
        return {"vendor": invocation.claims.vendor}

    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    install_broker_error_handlers(app)
    app.include_router(router)
    # A high threshold: an auth-failure event would need the admin database.
    app.state.ctx = SimpleNamespace(
        config=SimpleNamespace(
            cli_facades=facades,
            security=SimpleNamespace(auth_failure_event_threshold=1_000_000),
        )
    )
    app.state.broker_cli_invocation_validator = CachedCliInvocationValidator(
        resolver=resolver, cache_ttl_seconds=60.0
    )
    return TestClient(app, raise_server_exceptions=False)


def _facades(**vendors: bool) -> CliFacadesConfig:
    return CliFacadesConfig.model_validate(
        {
            "enabled": True,
            "vendors": {
                "cloudflare": {"enabled": vendors.get("cloudflare", False)},
                "github": {"enabled": vendors.get("github", False), "host": "gh.jentic.test"},
            },
        }
    )


@pytest.mark.parametrize("vendor", ["github", "cloudflare"])
def test_proxy_dependency_accepts_a_token_of_any_served_vendor(vendor: str) -> None:
    client = _proxy_client(
        _FakeResolver(_resolution(vendor=vendor)), _facades(github=True, cloudflare=True)
    )
    resp = client.get("/proxy", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"vendor": vendor}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer at_agent-access-token"},
        {"Authorization": "Bearer jak_agent-api-key"},
        {"x-jentic-api-key": _TOKEN},
    ],
)
def test_proxy_dependency_refuses_anything_but_a_cit_token(headers: dict[str, str]) -> None:
    resolver = _FakeResolver(_resolution())
    resp = _proxy_client(resolver, _facades(github=True)).get("/proxy", headers=headers)
    assert resp.status_code == 401
    assert resolver.calls == []


def test_proxy_dependency_answers_404_while_the_tokens_vendor_is_switched_off() -> None:
    client = _proxy_client(_FakeResolver(_resolution(vendor="github")), _facades(cloudflare=True))
    resp = client.get("/proxy", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 404
    assert resp.json()["type"] == "cli_facade_not_found"


def test_proxy_dependency_requires_the_execute_permission() -> None:
    resolution = _resolution()
    resolution = resolution.model_copy(
        update={"identity": resolution.identity.model_copy(update={"permissions": []})}
    )
    client = _proxy_client(_FakeResolver(resolution), _facades(github=True))
    resp = client.get("/proxy", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 403
    assert resp.json()["type"] == "insufficient_scope"
