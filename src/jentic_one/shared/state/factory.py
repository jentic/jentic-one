"""Factory + config for the shared-state backend.

``build_state_backend`` is called at app startup by each surface that keeps
shared state (auth, broker, control); the resulting backend is closed at
shutdown. Within a surface the rate limiters, circuit breaker, idempotency
store and single-use markers receive the same instance.

The ``BackendKind.REDIS`` path **fails fast at startup** when no ``redis_url`` is
configured, and the redis implementation is imported lazily so the default
memory path needs no ``redis`` dependency.
"""

from __future__ import annotations

from enum import StrEnum

import structlog
from pydantic import BaseModel, Field, SecretStr

from jentic_one.shared.state.backend import (
    DEFAULT_MEMORY_MAX_BYTES,
    DEFAULT_MEMORY_MAX_ENTRIES,
    DEFAULT_MEMORY_MAX_TOTAL_BYTES,
    MemoryStateBackend,
    NamespaceBudget,
    SharedStateBackend,
)

_logger = structlog.get_logger(__name__)

_DEFAULT_REDIS_KEY_PREFIX = "jentic:broker:"


class BackendKind(StrEnum):
    """Which shared-state implementation to build.

    Defined here as the single source of truth; consumers import it from this
    package rather than defining a second enum.
    """

    MEMORY = "memory"
    REDIS = "redis"


# Built-in budgets for the broker's ``Idempotency-Key`` entries. ``idem``
# holds one small record per key (status, fingerprint, headers capped at
# 512 bytes: about 1.2 KiB budgeted each); ``idem-body`` holds the stored
# response bodies, so a full body budget only drops bodies from replays and
# never refuses a claim. Each caller may hold at most a tenth of either, so
# one caller cannot lock every other caller out. With the 16 MiB default for
# every other namespace they fit the 192 MiB backend-wide cap, which the
# broker chart's 512Mi memory limit holds with the process baseline.
IDEM_OWNER_SHARE = 0.1
_BUILTIN_NAMESPACE_BUDGETS: dict[str, NamespaceBudget] = {
    "idem": NamespaceBudget(
        max_entries=250_000, max_bytes=128 * 1024 * 1024, max_owner_share=IDEM_OWNER_SHARE
    ),
    "idem-body": NamespaceBudget(
        max_entries=1_000_000, max_bytes=32 * 1024 * 1024, max_owner_share=IDEM_OWNER_SHARE
    ),
}

# Built-in per-owner shares for namespaces sized by ``max_entries_per_namespace``
# / ``max_bytes_per_namespace``. ``login-csrf`` holds one nonce per
# unauthenticated login page load, owned by the requesting client's network
# (``login-csrf:<client-key>:<nonce>``; IPv4 address or IPv6 /64, keyed so the
# address itself is not stored). A record is about 900 bytes budgeted (an
# 87-byte key, a 64-byte value and the per-entry overhead), so the 16 MiB
# default holds about 18,600 live nonces and a 0.02 share about 370 per
# client within the 5-minute nonce TTL. That is twice what the default
# ``auth.oauth_rate_limit`` authorize limiter (burst 30 + 30/min, keyed per
# client network the same way) lets one network mint in a TTL, so a large
# shared NAT or corporate egress IP hits the rate limiter before its share,
# while a single host flooding the page (including one rotating addresses
# inside its IPv6 /64) is refused without filling the namespace for everyone
# else.
LOGIN_CSRF_OWNER_SHARE = 0.02
_BUILTIN_OWNER_SHARES: dict[str, float] = {"login-csrf": LOGIN_CSRF_OWNER_SHARE}


class MemoryNamespaceBudgetConfig(BaseModel):
    """Entry-count and approximate byte budget for one memory-backend key namespace."""

    max_entries: int = Field(ge=1, description="Most live entries the namespace may hold.")
    max_bytes: int = Field(
        ge=1,
        description=(
            "Most approximate bytes (key + value + about 750 bytes of per-entry "
            "overhead) the namespace may hold."
        ),
    )
    max_owner_share: float | None = Field(
        default=None,
        gt=0,
        le=1,
        description=(
            "Most of max_entries and max_bytes that one owner (the key's second "
            "``:`` segment: for idem and idem-body the calling actor, for "
            "login-csrf the client's IPv4 address or IPv6 /64) may hold; "
            "an owner over its share is refused without affecting the others. "
            "Unset, it is 0.1 for idem and idem-body, 0.02 for login-csrf and "
            "unlimited elsewhere; 1.0 lets one owner use the whole namespace."
        ),
    )


class MemoryBackendConfig(BaseModel):
    """Bounds for the in-process (``memory``) shared-state backend.

    Every key belongs to a namespace, its prefix up to the first ``:``
    (``idem`` for Idempotency-Key records, ``idem-body`` for their stored
    response bodies, ``auth`` for the auth surface's single-use markers,
    ``login-csrf`` for login CSRF nonces, ``cb`` for circuit-breaker counters,
    and so on). Each namespace has its own budget, so one key kind filling up
    never starves another. A namespace that is full of live entries refuses
    new writes (the request that needed it gets a 503) rather than evicting a
    live entry. Ignored by the redis backend.
    """

    max_entries_per_namespace: int = Field(
        default=DEFAULT_MEMORY_MAX_ENTRIES,
        ge=1,
        description=(
            "Default most live entries one key namespace may hold before its writes are refused."
        ),
    )
    max_bytes_per_namespace: int = Field(
        default=DEFAULT_MEMORY_MAX_BYTES,
        ge=1,
        description=(
            "Default most approximate bytes (key + value + about 750 bytes of "
            "per-entry overhead) one key namespace may hold before its writes are refused."
        ),
    )
    max_total_bytes: int = Field(
        default=DEFAULT_MEMORY_MAX_TOTAL_BYTES,
        ge=1,
        description=(
            "Most approximate bytes all namespaces of one memory backend may hold "
            "together (201326592, 192 MiB, by default); a write past it is refused "
            "like a full namespace, never by evicting a live entry. Each surface "
            "that keeps shared state (auth, broker, control) has its own backend, "
            "so a process holds up to this much per such surface. Raise the "
            "container memory limit together with it."
        ),
    )
    namespaces: dict[str, MemoryNamespaceBudgetConfig] = Field(
        default_factory=dict,
        description=(
            "Per-namespace budget overrides, keyed by namespace (the key prefix "
            "before the first ``:``). Unless overridden here, idem (Idempotency-Key "
            "records) is 250000 entries / 134217728 bytes (128 MiB) and idem-body "
            "(their stored response bodies) 1000000 entries / 33554432 bytes "
            "(32 MiB), each with a 0.1 per-caller share; login-csrf (login page "
            "CSRF nonces) uses max_entries_per_namespace / max_bytes_per_namespace "
            "with a 0.02 per-client share, about 370 live nonces per IPv4 address "
            "or IPv6 /64 at the defaults (about 900 bytes each, 5-minute TTL), "
            "twice what the default authorize rate limit lets one IP request in "
            "that time; every other namespace "
            "uses max_entries_per_namespace / max_bytes_per_namespace. The "
            "sustained idempotent request rate idem can hold is min(max_entries, "
            "max_bytes / record_bytes) / broker.idempotency.ttl_s, where "
            "record_bytes is about 1000 bytes plus the response headers (capped at "
            "512 bytes): about 30 req/s at the defaults and a 1 h TTL, and a tenth "
            "of that per caller. Above it, Idempotency-Key requests get 503 "
            "idempotency_unavailable; bodies (about 830 bytes + body length each) "
            "stop being stored for replay sooner, about 5 req/s of 1 KiB bodies, "
            "without refusing requests."
        ),
    )

    def budgets(self) -> dict[str, NamespaceBudget]:
        """The per-namespace budgets: built-in defaults, then configured overrides."""
        budgets = dict(_BUILTIN_NAMESPACE_BUDGETS)
        for name, builtin_share in _BUILTIN_OWNER_SHARES.items():
            budgets[name] = NamespaceBudget(
                max_entries=self.max_entries_per_namespace,
                max_bytes=self.max_bytes_per_namespace,
                max_owner_share=builtin_share,
            )
        for name, b in self.namespaces.items():
            share = b.max_owner_share
            if share is None and name in budgets:
                share = budgets[name].max_owner_share
            budgets[name] = NamespaceBudget(
                max_entries=b.max_entries, max_bytes=b.max_bytes, max_owner_share=share
            )
        return budgets


class StateBackendConfig(BaseModel):
    """Configuration for :func:`build_state_backend`."""

    backend: BackendKind = BackendKind.MEMORY
    redis_url: SecretStr | None = None
    redis_key_prefix: str = _DEFAULT_REDIS_KEY_PREFIX
    memory: MemoryBackendConfig = Field(default_factory=MemoryBackendConfig)


def build_state_backend(cfg: StateBackendConfig) -> SharedStateBackend:
    """Build the configured shared-state backend.

    Args:
        cfg: Backend selection plus Redis connection details.

    Returns:
        A concrete backend implementing all three state roles.

    Raises:
        RuntimeError: If ``BackendKind.REDIS`` is selected without a ``redis_url``,
            or if the optional ``redis`` dependency is not installed.
    """
    if cfg.backend is BackendKind.REDIS:
        if not cfg.redis_url:
            raise RuntimeError(f"{BackendKind.REDIS} backend requires redis_url")
        if cfg.redis_key_prefix == _DEFAULT_REDIS_KEY_PREFIX:
            _logger.warning(
                "redis state backend using the default key prefix; set a "
                "per-environment redis_key_prefix to avoid cross-deployment "
                "key collisions on a shared Redis cluster",
                redis_key_prefix=cfg.redis_key_prefix,
            )
        # Imported lazily so the default memory path needs no ``redis`` dependency.
        # ``redis`` ships as an optional extra (``pip install jentic-one[redis]``),
        # so surface an actionable error rather than a bare ImportError.
        try:
            from jentic_one.shared.state.redis import RedisStateBackend
        except ImportError as exc:
            raise RuntimeError(
                f"{BackendKind.REDIS} backend requires the optional 'redis' "
                "dependency; install it with `pip install jentic-one[redis]` "
                "(or `uv sync --extra redis`)"
            ) from exc

        return RedisStateBackend(cfg.redis_url.get_secret_value(), key_prefix=cfg.redis_key_prefix)
    return MemoryStateBackend(
        max_entries=cfg.memory.max_entries_per_namespace,
        max_bytes=cfg.memory.max_bytes_per_namespace,
        max_total_bytes=cfg.memory.max_total_bytes,
        namespace_budgets=cfg.memory.budgets(),
    )
