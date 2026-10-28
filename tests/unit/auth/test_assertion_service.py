"""Unit tests for AssertionService.

The ``jti`` replay record is stubbed here (``_claim_jti``); its behaviour
against a real admin database (replay across service instances, expiry,
concurrent claims) is pinned by
``tests/integration/auth/test_assertion_jti_replay.py``.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

from jentic_one.auth.services import assertion_service
from jentic_one.auth.services.assertion_service import AssertionService
from jentic_one.auth.services.errors import InvalidGrantError


@pytest.fixture(autouse=True)
def claimed_jtis(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, float]]:
    """Stub the admin-DB replay record: every claim succeeds and is recorded."""
    claims: list[tuple[str, str, float]] = []

    async def _claim(session: Any, *, issuer: str, jti: str, exp: float) -> bool:
        claims.append((issuer, jti, exp))
        return True

    monkeypatch.setattr(AssertionService, "_claim_jti", staticmethod(_claim))
    return claims


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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.TokenService")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_active_agent_with_inactive_owner_stays_generic(
    mock_agent_repo: MagicMock, mock_token_svc_cls: MagicMock
) -> None:
    """An active agent whose owner is disabled or deleted mints no tokens and
    gets the same generic rejection as a disabled agent."""
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.owner_id = "usr_inactive_owner"
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, False))

    svc = AssertionService(ctx)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(_make_assertion(private_key, jti="owner-off-jti"))
    mock_token_svc_cls.return_value.issue_pair.assert_not_called()


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_bad_signature(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    _, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, exp=time.time() - 10)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_wrong_audience(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, aud="https://wrong.example.com/oauth/token")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_a_jti_whose_record_is_already_claimed(
    mock_agent_repo: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused replay-record claim is a generic rejection, before any token is minted."""
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(_make_active_agent(jwks), True))
    monkeypatch.setattr(AssertionService, "_claim_jti", staticmethod(AsyncMock(return_value=False)))

    with (
        patch("jentic_one.auth.services.assertion_service.TokenService") as token_cls,
        pytest.raises(InvalidGrantError, match="Assertion is invalid"),
    ):
        await AssertionService(ctx).verify_and_exchange(_make_assertion(private_key))
    token_cls.assert_not_called()


@patch("jentic_one.auth.services.assertion_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_verify_claims_the_jti_for_the_verified_issuer(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_permission_repo: MagicMock,
    claimed_jtis: list[tuple[str, str, float]],
) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))
    mock_permission_repo.list_for_actor = AsyncMock(return_value=[])
    mock_token_svc_cls.return_value = MagicMock(issue_pair=AsyncMock(return_value=("at", "rt")))
    exp = time.time() + 60

    await AssertionService(ctx).verify_and_exchange(
        _make_assertion(private_key, jti="the-jti", exp=exp)
    )

    assert claimed_jtis == [("agnt_test123", "the-jti", exp)]


@pytest.mark.parametrize("status", ["pending", "rejected"])
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_a_non_active_agent_writes_no_jti_record(
    mock_agent_repo: MagicMock, status: str, claimed_jtis: list[tuple[str, str, float]]
) -> None:
    """Approval polling (a fresh jti per attempt) never writes replay records."""
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = status
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(agent, True))

    with pytest.raises(InvalidGrantError):
        await AssertionService(ctx).verify_and_exchange(_make_assertion(private_key))
    assert claimed_jtis == []


def test_the_jti_record_stores_a_digest_never_the_claim() -> None:
    digest = assertion_service.jti_digest("some-jti")
    assert len(digest) == 64
    assert "some-jti" not in digest
    assert digest == assertion_service.jti_digest("some-jti")
    assert digest != assertion_service.jti_digest("some-jti2")


def test_a_lone_surrogate_jti_hashes_rather_than_erroring() -> None:
    """A signed claim of ``"\\ud800"`` decodes to a str strict UTF-8 cannot encode."""
    digest = assertion_service.jti_digest("\ud800")
    assert len(digest) == 64
    assert digest != assertion_service.jti_digest("\ud801")


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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=None)

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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(_make_active_agent(jwks), True))
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
    mock_agent_repo.get_with_owner_active = AsyncMock(return_value=(_make_active_agent(jwks), True))

    svc = AssertionService(ctx)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(
            _make_assertion(private_key, aud="http://evil.example:8000/oauth/token"),
            request_base_url="http://192.168.1.20:8000",
        )
