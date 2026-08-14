"""Strategy registry for search backends.

Maps (dialect, mode) pairs to SearchStrategy implementations.  Use
:func:`resolve_strategy` to obtain the correct strategy for the active
backend and configuration.
"""

from __future__ import annotations

from jentic_one.registry.repos.search.errors import SearchUnsupportedError
from jentic_one.registry.repos.search.protocol import SearchStrategy
from jentic_one.shared.config import SearchConfig
from jentic_one.shared.db.backends.base import DatabaseBackend

_STRATEGIES: dict[tuple[str, str], type[SearchStrategy]] = {}


def register_strategy[T: type[SearchStrategy]](cls: T) -> T:
    """Class decorator that registers a SearchStrategy by (dialect, name).

    Idempotent for an identical re-registration (same class — safe under double
    import); raises ``ValueError`` when the key is already bound to a *different*
    class, so an extension can't silently shadow a built-in strategy. Same
    posture as the other registration seams: ``register_config``
    (``shared/config.py``), ``register_telemetry_event``
    (``shared/telemetry/events.py``), ``register_target``
    (``migrations/targets.py``), and ``register_pipeline_stage``
    (``registry/ingest/pipeline/stage_registry.py``).
    """
    key = (cls.dialect, cls.name)
    existing = _STRATEGIES.get(key)
    if existing is not None and existing is not cls:
        msg = (
            f"Search strategy {key!r} is already registered by "
            f"{existing.__module__}.{existing.__qualname__}; refusing to shadow it "
            f"with {cls.__module__}.{cls.__qualname__}"
        )
        raise ValueError(msg)
    _STRATEGIES[key] = cls
    return cls


def resolve_strategy(backend: DatabaseBackend, config: SearchConfig) -> SearchStrategy:
    """Return an instantiated strategy for the given backend and config."""
    key = (backend.dialect_name, config.search_mode)
    strategy_cls = _STRATEGIES.get(key)
    if strategy_cls is None:
        available = [m for (d, m) in _STRATEGIES if d == backend.dialect_name]
        raise SearchUnsupportedError(
            f"No search strategy for ({backend.dialect_name}, {config.search_mode}). "
            f"Available modes for {backend.dialect_name}: {available}"
        )
    return strategy_cls()
