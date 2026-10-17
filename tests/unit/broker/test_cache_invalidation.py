"""Unit tests: the broker's caches drop the right entries on invalidation messages.

Drives the real cache classes through ``subscribe_broker_caches`` on an
``InProcessInvalidationBus`` — the same handlers the PostgreSQL bus feeds.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.broker.core.cache_invalidation import BROKER_TOPICS, subscribe_broker_caches
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.repos.agent_rule_evaluator import AgentRuleEvaluator
from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import CredentialDerivation
from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.invalidation import InProcessInvalidationBus, InvalidationMessage
from jentic_one.shared.models import ActorType
from jentic_one.shared.permissions.evaluation import PermissionRule


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class _TableResolver:
    """Token resolver backed by a dict (token → identity), counting lookups."""

    def __init__(self, table: dict[str, Identity | None]) -> None:
        self.table = table
        self.calls = 0

    async def resolve_access_token(self, token: str) -> Identity | None:
        self.calls += 1
        return self.table.get(token)


def _identity(sub: str, *, client: str | None = None, grant: str | None = None) -> Identity:
    return Identity(
        sub=sub,
        actor_type=ActorType.AGENT,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        oauth_client_id=client,
        oauth_grant_id=grant,
    )


class _CountingDeriver:
    def __init__(self) -> None:
        self.calls = 0

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        self.calls += 1
        return CredentialDerivation(
            credentials=(), agent_bound_any=False, api_served=False, identity_mismatch=None
        )


class _StubRules(AgentRuleEvaluator):
    """Rule evaluator whose rule read is a counter (the cache under test is real).

    The session is never connected: ``_fetch_rules`` is the only DB touchpoint.
    """

    def __init__(self) -> None:
        unconnected = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
        super().__init__(unconnected, cache_ttl_seconds=300)
        self.fetches = 0

    async def _fetch_rules(
        self, *, agent_id: str, credential_id: str, rule_set_id: str | None
    ) -> list[PermissionRule]:
        self.fetches += 1
        return []


class _Caches:
    def __init__(self) -> None:
        self.tokens = _TableResolver(
            {
                "at_a1": _identity("agnt_1"),
                "at_a2": _identity("agnt_2"),
                "at_c": _identity("usr_1", client="cli_1", grant="ocg_1"),
            }
        )
        self.keys = _TableResolver({"jak_1": _identity("agnt_1"), "jak_2": _identity("agnt_2")})
        self.access = CachedTokenValidator(resolver=self.tokens, cache_ttl_seconds=300)
        self.api_keys = CachedTokenValidator(resolver=self.keys, cache_ttl_seconds=300)
        self.rules = _StubRules()
        self.inner_deriver = _CountingDeriver()
        self.bindings = CachingCredentialDeriver(self.inner_deriver, cache_ttl_seconds=300)
        self.bus = InProcessInvalidationBus()
        subscribe_broker_caches(
            self.bus,
            access_tokens=self.access,
            api_keys=self.api_keys,
            rules=self.rules,
            bindings=self.bindings,
        )

    async def warm(self) -> None:
        await self.bus.start()
        for token in ("at_a1", "at_a2", "at_c"):
            await self.access.validate(token)
        for key in ("jak_1", "jak_2"):
            await self.api_keys.validate(key)
        for agent in ("agnt_1", "agnt_2"):
            await self.rules.evaluate(
                agent_id=agent,
                credential_id="cred_1",
                rule_set_id=None,
                method="GET",
                path="/",
                operation_id=None,
            )
            await self.bindings.derive_credentials(
                agent_id=agent, vendor="v", name="n", version="1"
            )
        await self.rules.evaluate(
            agent_id="agnt_1",
            credential_id="cred_1",
            rule_set_id="prs_1",
            method="GET",
            path="/",
            operation_id=None,
        )

    async def send(self, topic: str, **keys: list[str]) -> None:
        await self.bus.publish(
            InvalidationMessage(
                topic=topic, operation="UPDATE", keys={k: tuple(v) for k, v in keys.items()}
            )
        )

    def cached_tokens(self) -> set[str]:
        return {t for t in ("at_a1", "at_a2", "at_c") if _digest(t) in self.access._cache}

    def cached_keys(self) -> set[str]:
        return {k for k in ("jak_1", "jak_2") if _digest(k) in self.api_keys._cache}

    def cached_binding_agents(self) -> set[str]:
        return {k.split("\x00")[0] for k in self.bindings._cache}

    def cached_rule_keys(self) -> set[str]:
        return set(self.rules._cache)


@pytest.fixture()
async def caches() -> _Caches:
    c = _Caches()
    await c.warm()
    return c


async def test_token_revoke_drops_only_that_token(caches: _Caches) -> None:
    await caches.send("admin.access_tokens", token_hash=[_digest("at_a1")], actor_id=["agnt_1"])
    assert caches.cached_tokens() == {"at_a2", "at_c"}
    before = caches.tokens.calls
    await caches.access.validate("at_a1")
    assert caches.tokens.calls == before + 1, "a dropped entry is re-read from the DB"


async def test_api_key_change_drops_the_key_and_its_agent(caches: _Caches) -> None:
    await caches.send(
        "admin.agent_credentials", api_key_hash=[_digest("jak_1")], agent_id=["agnt_1"]
    )
    assert caches.cached_keys() == {"jak_2"}


async def test_agent_change_drops_its_tokens_keys_and_bindings(caches: _Caches) -> None:
    await caches.send("admin.agents", id=["agnt_2"])
    assert caches.cached_tokens() == {"at_a1", "at_c"}
    assert caches.cached_keys() == {"jak_1"}
    assert caches.cached_binding_agents() == {"agnt_1"}


async def test_grant_change_drops_the_actors_tokens_and_keys(caches: _Caches) -> None:
    await caches.send("admin.actor_permission_grants", actor_id=["agnt_1"])
    assert caches.cached_tokens() == {"at_a2", "at_c"}
    assert caches.cached_keys() == {"jak_2"}


@pytest.mark.parametrize(
    ("topic", "keys"),
    [
        ("admin.users", {"id": ["usr_1"]}),
        ("admin.oauth_clients", {"client_id": ["cli_1"]}),
        ("admin.oauth_client_grants", {"id": ["ocg_1"]}),
    ],
)
async def test_user_client_and_grant_changes_drop_matching_tokens(
    caches: _Caches, topic: str, keys: dict[str, list[str]]
) -> None:
    await caches.send(topic, **keys)
    assert caches.cached_tokens() == {"at_a1", "at_a2"}


async def test_binding_change_drops_the_agents_derivations(caches: _Caches) -> None:
    await caches.send("admin.agent_credential_bindings", agent_id=["agnt_1"], credential_id=["c"])
    assert caches.cached_binding_agents() == {"agnt_2"}


async def test_credential_change_drops_every_derivation(caches: _Caches) -> None:
    await caches.send("control.credentials", id=["cred_1"])
    assert caches.cached_binding_agents() == set()


async def test_rule_changes_drop_the_binding_or_rule_set(caches: _Caches) -> None:
    await caches.send(
        "control.agent_permission_rules", agent_id=["agnt_1"], credential_id=["cred_1"]
    )
    assert caches.cached_rule_keys() == {"binding\x00agnt_2\x00cred_1", "set\x00prs_1"}
    await caches.send("control.permission_rule_set_rules", rule_set_id=["prs_1"])
    assert caches.cached_rule_keys() == {"binding\x00agnt_2\x00cred_1"}


@pytest.mark.parametrize("topic", sorted(BROKER_TOPICS))
async def test_flush_on_any_topic_leaves_no_stale_entry_for_it(caches: _Caches, topic: str) -> None:
    await caches.bus.publish(InvalidationMessage(topic=topic, flush=True))
    affected = {
        "admin.access_tokens": caches.cached_tokens,
        "admin.agent_credentials": caches.cached_keys,
        "admin.agents": caches.cached_binding_agents,
        "admin.users": caches.cached_tokens,
        "admin.actor_permission_grants": caches.cached_keys,
        "admin.oauth_clients": caches.cached_tokens,
        "admin.oauth_client_grants": caches.cached_tokens,
        "admin.agent_credential_bindings": caches.cached_binding_agents,
        "control.agent_permission_rules": caches.cached_rule_keys,
        "control.permission_rule_set_rules": caches.cached_rule_keys,
        "control.credentials": caches.cached_binding_agents,
    }[topic]
    assert affected() == set()


async def test_reconnect_flush_empties_every_cache(caches: _Caches) -> None:
    await caches.bus.simulate_reconnect()
    assert caches.cached_tokens() == set()
    assert caches.cached_keys() == set()
    assert caches.cached_rule_keys() == set()
    assert caches.cached_binding_agents() == set()


async def test_negative_entries_drop_on_actor_change() -> None:
    """A key that resolved to nothing must be re-read once its agent changes."""
    resolver = _TableResolver({})
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300)
    with pytest.raises(Exception, match="unknown_token"):
        await validator.validate("jak_new")
    validator.invalidate_matching(lambda identity: identity.sub == "agnt_9")
    assert _digest("jak_new") not in validator._cache


async def test_invalidation_during_a_read_is_not_overwritten() -> None:
    """A verdict read before a concurrent invalidation is served but never cached."""
    gate = asyncio.Event()

    class _SlowResolver:
        async def resolve_access_token(self, token: str) -> Identity | None:
            await gate.wait()
            return _identity("agnt_1")

    validator = CachedTokenValidator(resolver=_SlowResolver(), cache_ttl_seconds=300)
    pending = asyncio.create_task(validator.validate("at_slow"))
    await asyncio.sleep(0)
    validator.invalidate("at_slow")
    gate.set()
    assert (await pending).sub == "agnt_1"
    assert _digest("at_slow") not in validator._cache
