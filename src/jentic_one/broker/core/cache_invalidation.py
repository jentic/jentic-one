"""Subscribe the broker's caches to the ``InvalidationBus``.

Each cache drops exactly the entries a source-table change can affect when the
key maps cheaply, and everything it holds when it does not (or when the bus
reports a ``flush``: a reconnect, or a payload too large to carry its keys).
The cache TTLs stay in force underneath — this only makes a change visible
sooner, never later.

| Topic | Cache | Dropped |
| ----- | ----- | ------- |
| ``admin.access_tokens`` | opaque-token | the token's digest (``token_hash``) |
| ``admin.agent_credentials`` | API-key | the key's digest (``api_key_hash``) |
| ``admin.agents`` | opaque-token, API-key, binding | the agent's entries |
| ``admin.users`` | opaque-token | the user's entries |
| ``admin.actor_permission_grants`` | opaque-token, API-key | the actor's entries |
| ``admin.oauth_clients`` | opaque-token | entries issued to the client |
| ``admin.oauth_client_grants`` | opaque-token | entries minted under the grant |
| ``admin.agent_credential_bindings`` | binding | the agent's derivations |
| ``control.agent_permission_rules`` | rule | the ``(agent, credential)`` rule list |
| ``control.permission_rule_set_rules`` | rule | the rule set's list |
| ``control.credentials`` | binding | everything (coverage is not keyed by credential) |
"""

from __future__ import annotations

from collections.abc import Callable

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.repos.agent_rule_evaluator import AgentRuleEvaluator
from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.invalidation import InvalidationBus, InvalidationMessage, topic_for

#: Every topic the broker subscribes to (pinned against the trigger migrations).
BROKER_TOPICS: frozenset[str] = frozenset(
    {
        topic_for("admin", "access_tokens"),
        topic_for("admin", "agent_credentials"),
        topic_for("admin", "agents"),
        topic_for("admin", "users"),
        topic_for("admin", "actor_permission_grants"),
        topic_for("admin", "oauth_clients"),
        topic_for("admin", "oauth_client_grants"),
        topic_for("admin", "agent_credential_bindings"),
        topic_for("control", "agent_permission_rules"),
        topic_for("control", "permission_rule_set_rules"),
        topic_for("control", "credentials"),
    }
)


def _by_actor(ids: tuple[str, ...]) -> Callable[[Identity], bool]:
    wanted = frozenset(ids)
    return lambda identity: identity.sub in wanted


def _by_oauth_client(ids: tuple[str, ...]) -> Callable[[Identity], bool]:
    wanted = frozenset(ids)
    return lambda identity: identity.oauth_client_id in wanted


def _by_oauth_grant(ids: tuple[str, ...]) -> Callable[[Identity], bool]:
    wanted = frozenset(ids)
    return lambda identity: identity.oauth_grant_id in wanted


def subscribe_broker_caches(
    bus: InvalidationBus,
    *,
    access_tokens: CachedTokenValidator,
    api_keys: CachedTokenValidator,
    rules: AgentRuleEvaluator,
    bindings: CachingCredentialDeriver | None,
) -> None:
    """Register the handlers that keep the broker's caches in step with the DB.

    ``bindings`` is ``None`` when the binding cache is disabled
    (``broker.toolkit_cache_ttl_s <= 0``): there is nothing to drop.
    """

    def on_access_tokens(msg: InvalidationMessage) -> None:
        if msg.flush:
            access_tokens.clear()
        else:
            access_tokens.invalidate_digests(msg.values("token_hash"))

    def on_agent_credentials(msg: InvalidationMessage) -> None:
        if msg.flush:
            api_keys.clear()
            return
        api_keys.invalidate_digests(msg.values("api_key_hash"))
        api_keys.invalidate_matching(_by_actor(msg.values("agent_id")))

    def on_agents(msg: InvalidationMessage) -> None:
        if msg.flush:
            access_tokens.clear()
            api_keys.clear()
            if bindings is not None:
                bindings.clear()
            return
        ids = msg.values("id")
        access_tokens.invalidate_matching(_by_actor(ids))
        api_keys.invalidate_matching(_by_actor(ids))
        if bindings is not None:
            bindings.invalidate_agents(ids)

    def on_users(msg: InvalidationMessage) -> None:
        if msg.flush:
            access_tokens.clear()
        else:
            access_tokens.invalidate_matching(_by_actor(msg.values("id")))

    def on_grants(msg: InvalidationMessage) -> None:
        if msg.flush:
            access_tokens.clear()
            api_keys.clear()
            return
        ids = msg.values("actor_id")
        access_tokens.invalidate_matching(_by_actor(ids))
        api_keys.invalidate_matching(_by_actor(ids))

    def on_oauth_clients(msg: InvalidationMessage) -> None:
        if msg.flush:
            access_tokens.clear()
        else:
            access_tokens.invalidate_matching(_by_oauth_client(msg.values("client_id")))

    def on_oauth_grants(msg: InvalidationMessage) -> None:
        if msg.flush:
            access_tokens.clear()
        else:
            access_tokens.invalidate_matching(_by_oauth_grant(msg.values("id")))

    def on_bindings(msg: InvalidationMessage) -> None:
        if bindings is None:
            return
        if msg.flush:
            bindings.clear()
        else:
            bindings.invalidate_agents(msg.values("agent_id"))

    def on_agent_rules(msg: InvalidationMessage) -> None:
        if msg.flush:
            rules.clear()
        else:
            rules.invalidate_bindings(msg.values("agent_id"), msg.values("credential_id"))

    def on_rule_set_rules(msg: InvalidationMessage) -> None:
        if msg.flush:
            rules.clear()
        else:
            rules.invalidate_rule_sets(msg.values("rule_set_id"))

    def on_credentials(_msg: InvalidationMessage) -> None:
        if bindings is not None:
            bindings.clear()

    handlers: dict[str, Callable[[InvalidationMessage], None]] = {
        topic_for("admin", "access_tokens"): on_access_tokens,
        topic_for("admin", "agent_credentials"): on_agent_credentials,
        topic_for("admin", "agents"): on_agents,
        topic_for("admin", "users"): on_users,
        topic_for("admin", "actor_permission_grants"): on_grants,
        topic_for("admin", "oauth_clients"): on_oauth_clients,
        topic_for("admin", "oauth_client_grants"): on_oauth_grants,
        topic_for("admin", "agent_credential_bindings"): on_bindings,
        topic_for("control", "agent_permission_rules"): on_agent_rules,
        topic_for("control", "permission_rule_set_rules"): on_rule_set_rules,
        topic_for("control", "credentials"): on_credentials,
    }
    for topic, handler in handlers.items():
        bus.subscribe(topic, handler)
