"""Factory + config for the shared-state backend.

``build_state_backend`` is called once at app startup; the resulting backend is
closed in the lifespan teardown. The rate limiter, circuit breaker, and
idempotency store all receive the same instance.

The ``BackendKind.REDIS`` path **fails fast at startup** when no ``redis_url`` is
configured, and the redis implementation is imported lazily so the default
memory path needs no ``redis`` dependency.
"""

from __future__ import annotations

from enum import StrEnum

import structlog
from pydantic import BaseModel, SecretStr

from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend

_logger = structlog.get_logger(__name__)

_DEFAULT_REDIS_KEY_PREFIX = "jentic:broker:"


class BackendKind(StrEnum):
    """Which shared-state implementation to build.

    Defined here as the single source of truth; consumers import it from this
    package rather than defining a second enum.
    """

    MEMORY = "memory"
    REDIS = "redis"


class StateBackendConfig(BaseModel):
    """Configuration for :func:`build_state_backend`."""

    backend: BackendKind = BackendKind.MEMORY
    redis_url: SecretStr | None = None
    redis_key_prefix: str = _DEFAULT_REDIS_KEY_PREFIX


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
    return MemoryStateBackend()
