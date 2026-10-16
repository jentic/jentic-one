"""Unit tests for AssertionService."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from jwt.algorithms import OKPAlgorithm
from starlette.requests import Request

from jentic_one.auth.services import assertion_service
from jentic_one.auth.services.assertion_service import AssertionService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.routers.oauth import get_assertion_service
from jentic_one.shared.state.backend import (
    MemoryStateBackend,
    RateLimitDecision,
    StateBackendCapacityError,
)


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.config.auth.canonical_base_url = "https://auth.example.com"
    ctx.config.auth.assertion_max_ttl_seconds = 300
    ctx.config.auth.access_ttl_seconds = 3600
    ctx.config.auth.refresh_ttl_seconds = 604800
    return ctx


def _generate_keypair() -> tuple[Ed25519PrivateKey, dict[str, Any]]:
    """Generate an Ed25519 key pair and return (private_key, jwks_dict)."""
    private_key = Ed25519PrivateKey.generate()
    algo = OKPAlgorithm()
    jwk_dict = algo.to_jwk(private_key.public_key())
    if isinstance(jwk_dict, str):
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "test-key-1"
    jwks = {"keys": [jwk_dict]}
    return private_key, jwks


def _make_assertion(
    private_key: Ed25519PrivateKey,
    *,
    iss: str = "agnt_test123",
    aud: str = "https://auth.example.com/oauth/token",
    exp: float | None = None,
    jti: str = "unique-jti-1",
    kid: str = "test-key-1",
) -> str:
    now = time.time()
    payload = {
        "iss": iss,
        "aud": aud,
        "exp": exp if exp is not None else now + 60,
        "iat": now,
        "jti": jti,
    }
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers={"kid": kid})


def _make_active_agent(jwks: dict[str, Any]) -> MagicMock:
    agent = MagicMock()
    agent.id = "agnt_test123"
    agent.status = "active"
    agent.jwks = jwks
    return agent


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_verify_and_exchange_happy_path(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    grant = MagicMock()
    grant.permission = "read"
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[grant])

    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_new", "rt_new"))
    mock_token_svc_cls.return_value = token_svc_instance

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    access, refresh, scopes = await svc.verify_and_exchange(assertion)

    assert access == "at_new"
    assert refresh == "rt_new"
    assert scopes == ["read"]


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_pending_agent_gets_distinct_pending_detail(
    mock_agent_repo: MagicMock,
) -> None:
    """A pending agent with a VALID signature gets the distinct pending detail.

    This is what lets the CLI's register/setup/wizard approval wait poll instead
    of aborting: self-hosted backends mint no claim tokens, so the pending
    signal must be distinguishable from a hard assertion failure.
    """
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = "pending"
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="pending approval"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_pending_agent_bad_signature_stays_generic(
    mock_agent_repo: MagicMock,
) -> None:
    """Without proof of key possession the status is NOT revealed: a bad
    signature gets the generic rejection regardless of the agent's status."""
    ctx = _make_ctx()
    _, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = "pending"
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    other_private_key = Ed25519PrivateKey.generate()
    svc = AssertionService(ctx)
    assertion = _make_assertion(other_private_key)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(assertion)


@pytest.mark.parametrize("status", ["rejected", "disabled", "archived"])
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_terminal_statuses_stay_generic(
    mock_agent_repo: MagicMock, status: str
) -> None:
    """Terminal (non-waitable) statuses keep the generic rejection even with a
    valid signature — only PENDING gets a probe signal, because only PENDING
    is worth polling on."""
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = status
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_bad_signature(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    _, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    other_private_key = Ed25519PrivateKey.generate()
    svc = AssertionService(ctx)
    assertion = _make_assertion(other_private_key)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_expired_assertion(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, exp=time.time() - 10)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_wrong_audience(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, aud="https://wrong.example.com/oauth/token")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_verify_rejects_replayed_jti(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[])

    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_1", "rt_1"))
    mock_token_svc_cls.return_value = token_svc_instance

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, jti="same-jti")
    await svc.verify_and_exchange(assertion)

    assertion2 = _make_assertion(private_key, jti="same-jti")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion2)


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_replay_store_refuses_a_jti_first_seen_by_another_process(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The jti record lives on the shared-state store, not in process memory.

    Two ``server.workers`` processes share the store but not the in-process
    cache; a replay answered by the second must still be refused.
    """
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_active_agent(jwks))
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[])
    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_1", "rt_1"))
    mock_token_svc_cls.return_value = token_svc_instance
    shared = MemoryStateBackend()

    first_worker = AssertionService(ctx, replay_store=shared)
    await first_worker.verify_and_exchange(_make_assertion(private_key, jti="cross-worker-jti"))

    # A fresh process starts with an empty in-process cache.
    monkeypatch.setattr(assertion_service, "_jti_cache", None)
    second_worker = AssertionService(ctx, replay_store=shared)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await second_worker.verify_and_exchange(
            _make_assertion(private_key, jti="cross-worker-jti")
        )


class _RecordingStore:
    """A non-memory ``SharedStateBackend`` (a stand-in for redis) that records claims."""

    def __init__(self) -> None:
        self.claims: list[str] = []
        self._keys: set[str] = set()

    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        self._keys.add(key)

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=burst, retry_after_s=0.0, limit=burst)

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int:
        return amount

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        self.claims.append(key)
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    async def aclose(self) -> None:
        return None


def _request_with_backend(backend: object) -> Request:
    app = FastAPI()
    app.state.auth_state_backend = backend
    return Request({"type": "http", "app": app})


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_router_claims_jti_through_the_apps_shared_state_backend(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_assertion_service`` must hand ``app.state.auth_state_backend`` to the service.

    Without it every process falls back to its own cache and a replay sent to a
    second worker or replica is accepted.
    """
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_active_agent(jwks))
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[])
    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_1", "rt_1"))
    mock_token_svc_cls.return_value = token_svc_instance
    store = _RecordingStore()
    request = _request_with_backend(store)

    await get_assertion_service(request, ctx).verify_and_exchange(
        _make_assertion(private_key, jti="routed-jti")
    )
    assert store.claims == ["auth:jwt_bearer_jti:routed-jti"]

    # A second process: empty in-process cache, same shared store.
    monkeypatch.setattr(assertion_service, "_jti_cache", None)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await get_assertion_service(request, ctx).verify_and_exchange(
            _make_assertion(private_key, jti="routed-jti")
        )
    assert len(store.claims) == 2


def test_router_without_a_shared_state_backend_keeps_the_process_cache() -> None:
    svc = get_assertion_service(_request_with_backend(None), _make_ctx())
    assert svc._replay_store is None


async def test_verify_rejects_non_eddsa_algorithm() -> None:
    ctx = _make_ctx()
    svc = AssertionService(ctx)
    fake_token = jwt.encode({"iss": "test"}, "secret", algorithm="HS256")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(fake_token)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_unknown_agent(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, _ = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=None)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


def _make_zero_config_ctx() -> MagicMock:
    # Nothing pinned, all-interfaces bind: the request-less audience derives
    # from the bind (http://127.0.0.1:8000).
    ctx = _make_ctx()
    ctx.config.auth.canonical_base_url = ""
    ctx.config.server.public_base_url = ""
    ctx.config.server.host = "0.0.0.0"
    ctx.config.server.port = 8000
    return ctx


@pytest.mark.parametrize(
    "aud",
    [
        "http://127.0.0.1:8000/oauth/token",  # request-less bind origin
        "http://192.168.1.20:8000/oauth/token",  # the advertised token_endpoint
    ],
)
@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_verify_accepts_request_scoped_audience_zero_config(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_scope_repo: MagicMock,
    aud: str,
) -> None:
    """Zero-config, reached over a LAN address: the discovery token_endpoint
    (request-scoped) and the bind-derived audience are both accepted."""
    ctx = _make_zero_config_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_active_agent(jwks))
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[])
    mock_token_svc_cls.return_value = MagicMock(issue_pair=AsyncMock(return_value=("at", "rt")))

    svc = AssertionService(ctx)
    access, _, _ = await svc.verify_and_exchange(
        _make_assertion(private_key, aud=aud, jti=f"jti-{aud}"),
        request_base_url="http://192.168.1.20:8000",
    )
    assert access == "at"


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_audience_not_matching_request_or_bind(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_zero_config_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_active_agent(jwks))

    svc = AssertionService(ctx)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(
            _make_assertion(private_key, aud="http://evil.example:8000/oauth/token"),
            request_base_url="http://192.168.1.20:8000",
        )


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_jti_replay_is_refused_after_a_login_csrf_flood(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
) -> None:
    """Unauthenticated CSRF nonces cannot push a live jti record out of the store.

    ``GET /login`` mints a ``login-csrf:`` key per render. Flooding the store
    with them must neither evict the jti replay record (a replay would then be
    accepted) nor block the jti namespace.
    """
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_active_agent(jwks))
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[])
    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_1", "rt_1"))
    mock_token_svc_cls.return_value = token_svc_instance
    backend = MemoryStateBackend(max_entries=100)
    svc = AssertionService(ctx, replay_store=backend)

    await svc.verify_and_exchange(_make_assertion(private_key, jti="captured-jti"))

    refused = 0
    for i in range(150):
        try:
            await backend.set(f"login-csrf:nonce-{i}", b"digest", ttl_s=300.0)
        except StateBackendCapacityError:
            refused += 1
    assert refused == 50

    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(_make_assertion(private_key, jti="captured-jti"))
    # The jti namespace still accepts new assertions.
    await svc.verify_and_exchange(_make_assertion(private_key, jti="next-jti"))


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_exchange_is_refused_when_the_jti_record_cannot_be_stored(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
) -> None:
    """A full jti namespace refuses the exchange; it never mints unrecorded tokens."""
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_active_agent(jwks))
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[])
    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_1", "rt_1"))
    mock_token_svc_cls.return_value = token_svc_instance
    backend = MemoryStateBackend(max_entries=1)
    svc = AssertionService(ctx, replay_store=backend)
    await svc.verify_and_exchange(_make_assertion(private_key, jti="first"))
    token_svc_instance.issue_pair.reset_mock()

    with pytest.raises(StateBackendCapacityError):
        await svc.verify_and_exchange(_make_assertion(private_key, jti="second"))
    token_svc_instance.issue_pair.assert_not_awaited()
