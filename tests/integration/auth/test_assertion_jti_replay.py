"""JWT-bearer ``jti`` replay protection against a real admin database.

Pins #1842: the single-use ``jti`` record lives in the admin database, so a
replayed assertion is refused by any auth process sharing it, not only by the
process that accepted it first. Runs on both backends (``make
test-integration`` / ``make test-integration-sqlite``); the concurrent-claim
test needs PostgreSQL row locking and is skipped on SQLite, where the single
writer lock serialises claims by construction.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from sqlalchemy import delete, func, select, update

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.assertion_jtis import AssertionJti
from jentic_one.admin.repos.assertion_jti_repo import AssertionJtiRepository
from jentic_one.auth.services.assertion_service import (
    JTI_CLOCK_SKEW_GRACE_S,
    AssertionService,
    jti_digest,
)
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from tests.integration.auth.seeds import seed_agent, seed_user

pytestmark = pytest.mark.integration

_OWNER = "usr_jti_replay_owner"


@pytest.fixture()
async def clean_jtis(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(AssertionJti))

    await _clean()
    yield
    await _clean()


@pytest.fixture()
async def second_auth_process(integration_config: AppConfig) -> AsyncGenerator[Context, None]:
    """An independent Context (own pools, own state backend): a second auth process."""
    async with Context(integration_config) as ctx:
        yield ctx


def _keypair() -> tuple[Ed25519PrivateKey, dict[str, Any]]:
    key = Ed25519PrivateKey.generate()
    jwk = OKPAlgorithm().to_jwk(key.public_key())
    jwk_dict = json.loads(jwk) if isinstance(jwk, str) else dict(jwk)
    jwk_dict["kid"] = "k1"
    return key, {"keys": [jwk_dict]}


async def _active_agent(ctx: Context) -> tuple[str, Ed25519PrivateKey]:
    await seed_user(ctx, _OWNER)
    agent_id = await seed_agent(ctx, owner_id=_OWNER, scopes=[])
    key, jwks = _keypair()
    async with ctx.admin_db.transaction() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(jwks=jwks))
    return agent_id, key


def _assertion(
    ctx: Context, key: Ed25519PrivateKey, agent_id: str, *, jti: str, exp: float | None = None
) -> str:
    audience = f"{ctx.config.auth.canonical_base_url.rstrip('/')}/oauth/token"
    now = time.time()
    return jwt.encode(
        {
            "iss": agent_id,
            "aud": audience,
            "exp": exp if exp is not None else now + 60,
            "iat": now,
            "jti": jti,
        },
        key,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )


@pytest.fixture()
def canonical_auth(integration_context: Context) -> Iterator[None]:
    """Pin the audience the assertions are signed for; restored afterwards."""
    auth = integration_context.config.auth
    prior = auth.canonical_base_url
    auth.canonical_base_url = "https://auth.jti.test"
    yield
    auth.canonical_base_url = prior


async def _rows(ctx: Context) -> int:
    async with ctx.admin_db.session() as session:
        return int(
            (await session.execute(select(func.count()).select_from(AssertionJti))).scalar_one()
        )


async def test_a_jti_is_single_use_across_two_auth_processes(
    integration_context: Context,
    second_auth_process: Context,
    clean_jtis: None,
    canonical_auth: None,
) -> None:
    agent_id, key = await _active_agent(integration_context)
    assertion = _assertion(integration_context, key, agent_id, jti="jti-shared")

    access, refresh, _ = await AssertionService(integration_context).verify_and_exchange(assertion)
    assert access and refresh

    # The second process never saw the first exchange; the shared record refuses it.
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await AssertionService(second_auth_process).verify_and_exchange(assertion)
    # And a fresh assertion reusing the jti (a new signature, same claim) too.
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await AssertionService(second_auth_process).verify_and_exchange(
            _assertion(integration_context, key, agent_id, jti="jti-shared")
        )

    async with integration_context.admin_db.session() as session:
        row = (await session.execute(select(AssertionJti))).scalar_one()
    assert row.issuer == agent_id
    assert row.jti_digest == jti_digest("jti-shared")
    # Only the digest is stored, never the claim or the assertion.
    assert "jti-shared" not in (row.issuer, row.jti_digest)


async def test_the_same_jti_from_another_issuer_is_not_a_replay(
    integration_context: Context, clean_jtis: None, canonical_auth: None
) -> None:
    agent_a, key_a = await _active_agent(integration_context)
    agent_b = await seed_agent(integration_context, owner_id=_OWNER, scopes=[], name="agent-b")
    key_b, jwks_b = _keypair()
    async with integration_context.admin_db.transaction() as session:
        await session.execute(update(Agent).where(Agent.id == agent_b).values(jwks=jwks_b))
    svc = AssertionService(integration_context)

    await svc.verify_and_exchange(_assertion(integration_context, key_a, agent_a, jti="same"))
    await svc.verify_and_exchange(_assertion(integration_context, key_b, agent_b, jti="same"))

    assert await _rows(integration_context) == 2


async def test_the_record_outlives_the_assertion_by_the_skew_grace(
    integration_context: Context, clean_jtis: None, canonical_auth: None
) -> None:
    agent_id, key = await _active_agent(integration_context)
    exp = time.time() + 90

    await AssertionService(integration_context).verify_and_exchange(
        _assertion(integration_context, key, agent_id, jti="jti-exp", exp=exp)
    )

    async with integration_context.admin_db.session() as session:
        row = (await session.execute(select(AssertionJti))).scalar_one()
    expected = datetime.fromtimestamp(exp, UTC) + timedelta(seconds=JTI_CLOCK_SKEW_GRACE_S)
    assert abs((row.expires_at - expected).total_seconds()) < 1


async def test_an_expired_record_is_reusable_only_by_a_valid_assertion(
    integration_context: Context, clean_jtis: None, canonical_auth: None
) -> None:
    """Once the record has expired the jti is free again, for a valid assertion only.

    The original assertion is past its ``exp`` by then, so it still fails;
    a fresh, valid assertion reusing the ``jti`` takes the record over.
    """
    agent_id, key = await _active_agent(integration_context)
    svc = AssertionService(integration_context)
    await svc.verify_and_exchange(_assertion(integration_context, key, agent_id, jti="jti-reuse"))

    # Age the record (and the assertion it guards) past expiry.
    past = datetime.now(UTC) - timedelta(minutes=10)
    async with integration_context.admin_db.transaction() as session:
        await session.execute(update(AssertionJti).values(expires_at=past))
    stale = _assertion(integration_context, key, agent_id, jti="jti-reuse", exp=time.time() - 600)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(stale)

    access, _, _ = await svc.verify_and_exchange(
        _assertion(integration_context, key, agent_id, jti="jti-reuse")
    )
    assert access
    async with integration_context.admin_db.session() as session:
        row = (await session.execute(select(AssertionJti))).scalar_one()
    assert row.expires_at > datetime.now(UTC)


async def test_expired_records_are_pruned_in_bounded_batches(
    integration_context: Context, clean_jtis: None, canonical_auth: None
) -> None:
    past = datetime.now(UTC) - timedelta(minutes=10)
    future = datetime.now(UTC) + timedelta(minutes=10)
    async with integration_context.admin_db.transaction() as session:
        for i in range(5):
            session.add(AssertionJti(issuer="agnt_old", jti_digest=f"{i:064x}", expires_at=past))
        session.add(AssertionJti(issuer="agnt_live", jti_digest="f" * 64, expires_at=future))

    async with integration_context.admin_db.transaction() as session:
        deleted = await AssertionJtiRepository.delete_expired(
            session, now=datetime.now(UTC), limit=3
        )
    assert deleted == 3
    assert await _rows(integration_context) == 3

    # An exchange prunes the rest and keeps the live record.
    agent_id, key = await _active_agent(integration_context)
    await AssertionService(integration_context).verify_and_exchange(
        _assertion(integration_context, key, agent_id, jti="jti-prune")
    )
    async with integration_context.admin_db.session() as session:
        issuers = sorted((await session.execute(select(AssertionJti.issuer))).scalars())
    assert issuers == sorted(["agnt_live", agent_id])


async def test_a_rejected_exchange_leaves_no_record(
    integration_context: Context, clean_jtis: None, canonical_auth: None
) -> None:
    """The record is written in the verifying transaction, which a later failure rolls back."""
    agent_id, key = await _active_agent(integration_context)
    # Past the exp check but with a bad audience: fails after nothing is claimed.
    bad = jwt.encode(
        {
            "iss": agent_id,
            "aud": "https://elsewhere.test/oauth/token",
            "exp": time.time() + 60,
            "jti": "jti-bad",
        },
        key,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )
    with pytest.raises(InvalidGrantError):
        await AssertionService(integration_context).verify_and_exchange(bad)
    assert await _rows(integration_context) == 0


async def test_concurrent_claims_of_one_jti_yield_exactly_one_success(
    integration_context: Context,
    second_auth_process: Context,
    integration_config: AppConfig,
    clean_jtis: None,
) -> None:
    if integration_config.databases.admin.backend == "sqlite":
        pytest.skip("SQLite serialises writers; the race needs PostgreSQL row locking")

    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    first_inserted = asyncio.Event()
    release = asyncio.Event()

    async def _claim(ctx: Context, *, hold: bool) -> bool:
        async with ctx.admin_db.transaction() as session:
            won = await AssertionJtiRepository.claim(
                session,
                issuer="agnt_race",
                jti_digest=jti_digest("jti-race"),
                expires_at=expires_at,
                now=datetime.now(UTC),
            )
            if hold:
                # Keep the first transaction open so the second claim must wait
                # on its uncommitted row rather than see a committed one.
                first_inserted.set()
                await release.wait()
            return won

    first = asyncio.create_task(_claim(integration_context, hold=True))
    await first_inserted.wait()
    second = asyncio.create_task(_claim(second_auth_process, hold=False))
    await asyncio.sleep(0.2)
    assert not second.done(), "the second claim must block on the first's row lock"
    release.set()

    results = await asyncio.gather(first, second)
    assert sorted(results) == [False, True]
    assert await _rows(integration_context) == 1


async def test_many_simultaneous_claims_yield_exactly_one_success(
    integration_context: Context,
    second_auth_process: Context,
    integration_config: AppConfig,
    clean_jtis: None,
) -> None:
    if integration_config.databases.admin.backend == "sqlite":
        pytest.skip("SQLite serialises writers; the race needs PostgreSQL row locking")
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    async def _claim(ctx: Context) -> bool:
        async with ctx.admin_db.transaction() as session:
            return await AssertionJtiRepository.claim(
                session,
                issuer="agnt_race",
                jti_digest=jti_digest("jti-burst"),
                expires_at=expires_at,
                now=datetime.now(UTC),
            )

    contexts = [integration_context, second_auth_process] * 2
    results = await asyncio.gather(*(_claim(ctx) for ctx in contexts))
    assert results.count(True) == 1
    assert await _rows(integration_context) == 1
