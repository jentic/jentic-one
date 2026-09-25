"""Regression: every broker-facing agent-identity mint populates ``parent_actor_id``.

The forthcoming owner-based credential binding filter keys off
``Identity.parent_actor_id`` (the agent's owner). Any entry path that mints an
AGENT ``Identity`` without it silently downgrades to a "no owner match" and
either denies traffic that should be allowed or leaks traffic that should be
denied — depending which way the filter is oriented. These tests pin the shape
at each broker-facing seam a unit test can drive.

Seams covered:

- ``JwtTokenValidator`` (``broker/services/auth/token_validation``) — reads a
  ``parent_actor_id`` claim off a trusted issuer's signed JWT.
- ``ApiKeyResolver._lookup_agent`` (``shared/auth/api_key_resolver``) — reads
  ``agents.owner_id`` on the agent-arm hit.

The DB-backed opaque-token seam (``InProcessTokenResolver``) is exercised
end-to-end in ``tests/integration/broker/test_auth_e2e.py`` — no DB mocking here.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from jentic_one.broker.services.auth import JwtTokenValidator, JwtVerifier
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

_JWT_SECRET = "unit-test-secret-key"  # pragma: allowlist secret


def _sign(claims: dict[str, object]) -> str:
    return jwt.encode(claims, _JWT_SECRET, algorithm="HS256")


def _future_exp() -> int:
    return int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())


async def test_jwt_validator_populates_parent_actor_id_from_claim() -> None:
    """A trusted-issuer JWT that carries ``parent_actor_id`` propagates it onto Identity."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET))
    token = _sign(
        {
            "sub": "agnt_child",
            "exp": _future_exp(),
            "actor_type": ActorType.AGENT.value,
            "scopes": [BROKER_EXECUTE_SCOPE],
            "parent_actor_id": "usr_owner",
        }
    )

    identity = await validator.validate(token)

    assert identity.actor_type is ActorType.AGENT
    assert identity.parent_actor_id == "usr_owner"


async def test_jwt_validator_leaves_parent_actor_id_none_when_claim_absent() -> None:
    """No claim → None (not a silent default): the downstream owner filter
    treats a missing ``parent_actor_id`` as "no owner match" and never
    invents a value the trusted issuer did not vouch for."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET))
    token = _sign(
        {
            "sub": "agnt_orphan",
            "exp": _future_exp(),
            "actor_type": ActorType.AGENT.value,
            "scopes": [BROKER_EXECUTE_SCOPE],
        }
    )

    identity = await validator.validate(token)

    assert identity.parent_actor_id is None


async def test_jwt_validator_rejects_non_string_parent_actor_id() -> None:
    """A non-string ``parent_actor_id`` claim is not smuggled onto Identity —
    it degrades to None rather than raising the request past the edge."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_JWT_SECRET))
    token = _sign(
        {
            "sub": "agnt_child",
            "exp": _future_exp(),
            "actor_type": ActorType.AGENT.value,
            "parent_actor_id": 42,
        }
    )

    identity = await validator.validate(token)

    assert identity.parent_actor_id is None


# --- ApiKeyResolver -------------------------------------------------------
# The DB access is fronted by ``ApiKeyResolver`` which itself takes a
# ``DatabaseSession`` — a plain ``MagicMock()`` here (with no ``spec=`` on any
# DB symbol) satisfies ``tests/arch/test_no_db_mocking.py`` while still
# letting us assert the field wiring from the ``agents`` row.

Row = namedtuple("Row", ["scope"])
AgentRow = namedtuple("AgentRow", ["agent_id", "status", "owner_id"])


@pytest.fixture()
def admin_db() -> MagicMock:
    return MagicMock()


async def test_api_key_resolver_populates_parent_actor_id_from_owner_id(
    admin_db: MagicMock,
) -> None:
    """The ``jak_``/``sak_`` agent-arm mints Identity with ``agents.owner_id`` as parent."""
    agent_row = AgentRow(agent_id="agnt_child", status="active", owner_id="usr_owner")
    scope_rows = [Row(scope=BROKER_EXECUTE_SCOPE)]

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = agent_row
        else:
            result.all.return_value = scope_rows
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await ApiKeyResolver(admin_db).resolve("jak_secret_value")

    assert identity is not None
    assert identity.actor_type is ActorType.AGENT
    assert identity.parent_actor_id == "usr_owner"


async def test_api_key_resolver_ownerless_agent_yields_none_parent(
    admin_db: MagicMock,
) -> None:
    """An agent with no ``owner_id`` (schema allows NULL) does not synthesise a parent."""
    agent_row = AgentRow(agent_id="agnt_orphan", status="active", owner_id=None)
    scope_rows: list[Row] = []

    session_mock = AsyncMock()
    call_count = 0

    async def _execute(stmt: object, params: dict[str, object]) -> object:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.one_or_none.return_value = agent_row
        else:
            result.all.return_value = scope_rows
        return result

    session_mock.execute = _execute
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = session_mock
    ctx_mgr.__aexit__.return_value = None
    admin_db.session.return_value = ctx_mgr

    identity = await ApiKeyResolver(admin_db).resolve("jak_orphan_key")

    assert identity is not None
    assert identity.actor_type is ActorType.AGENT
    assert identity.parent_actor_id is None
