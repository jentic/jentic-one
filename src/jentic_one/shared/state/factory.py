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


# Built-in budget for the broker's ``Idempotency-Key`` records (``idem:``
# keys): a DONE record carries the stored response body (up to
# ``broker.idempotency.max_response_bytes``) for ``broker.idempotency.ttl_s``,
# so it needs far more room than the small claim and replay markers.
_BUILTIN_NAMESPACE_BUDGETS: dict[str, NamespaceBudget] = {
    "idem": NamespaceBudget(max_entries=1_000_000, max_bytes=256 * 1024 * 1024),
}


class MemoryNamespaceBudgetConfig(BaseModel):
    """Entry-count and approximate byte budget for one memory-backend key namespace."""

    max_entries: int = Field(ge=1, description="Most live entries the namespace may hold.")
    max_bytes: int = Field(
        ge=1,
        description=(
            "Most approximate bytes (key + value + a fixed per-entry overhead) "
            "the namespace may hold."
        ),
    )


class MemoryBackendConfig(BaseModel):
    """Bounds for the in-process (``memory``) shared-state backend.

    Every key belongs to a namespace, its prefix up to the first ``:``
    (``idem`` for Idempotency-Key records, ``auth`` for JWT-bearer jti replay
    records, ``login-csrf`` for login CSRF nonces, ``cb`` for circuit-breaker
    counters, and so on). Each namespace has its own budget, so one key kind
    filling up never starves another. A namespace that is full of live entries
    refuses new writes (the request that needed it gets a 503) rather than
    evicting a live entry. Ignored by the redis backend.
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
            "Default most approximate bytes (key + value + a fixed per-entry "
            "overhead) one key namespace may hold before its writes are refused."
        ),
    )
    namespaces: dict[str, MemoryNamespaceBudgetConfig] = Field(
        default_factory=dict,
        description=(
            "Per-namespace budget overrides, keyed by namespace (the key prefix "
            "before the first ``:``). ``idem`` defaults to 1000000 entries and "
            "268435456 bytes (256 MiB) unless overridden here. At most "
            "max_entries / broker.idempotency.ttl_s idempotent requests per "
            "second can be held, so size it (or lower the TTL) for your load."
        ),
    )

    def budgets(self) -> dict[str, NamespaceBudget]:
        """The per-namespace overrides: built-in defaults, then configured ones."""
        configured = {
            name: NamespaceBudget(max_entries=b.max_entries, max_bytes=b.max_bytes)
            for name, b in self.namespaces.items()
        }
        return {**_BUILTIN_NAMESPACE_BUDGETS, **configured}


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
        namespace_budgets=cfg.memory.budgets(),
    )
