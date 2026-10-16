"""Strategy registry for search backends.

Maps (dialect, mode) pairs to SearchStrategy implementations.  Use
:func:`resolve_strategy` to obtain the correct strategy for the active
backend and configuration, and :func:`resolve_strategy_by_name` to look up a
specific registered strategy (the composition seam for composite strategies).
"""

from __future__ import annotations

from jentic_one.registry.repos.search.errors import SearchUnsupportedError
from jentic_one.registry.repos.search.protocol import (
    ConfigurableSearchStrategy,
    SearchStrategy,
)
from jentic_one.shared.config import AppConfig, SearchConfig
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


def resolve_strategy(
    backend: DatabaseBackend,
    config: SearchConfig,
    *,
    app_config: AppConfig | None = None,
) -> SearchStrategy:
    """Return an instantiated strategy for the given backend and config.

    A strategy class that declares the :class:`ConfigurableSearchStrategy`
    ``from_config`` hook is built with ``from_config(config,
    app_config=app_config)``; any other class is built zero-arg. Raises
    ``SearchUnsupportedError`` when no strategy is registered for the
    ``(dialect, search_mode)`` pair, or when ``from_config`` rejects the config.
    """
    key = (backend.dialect_name, config.search_mode)
    strategy_cls = _lookup(*key)
    return build_strategy(strategy_cls, config, app_config=app_config)


def resolve_strategy_by_name(
    dialect: str,
    name: str,
    *,
    config: SearchConfig | None = None,
    app_config: AppConfig | None = None,
) -> SearchStrategy:
    """Return an instance of the strategy registered under ``(dialect, name)``.

    The composition seam for composite strategies: a strategy that fuses or
    wraps others (e.g. a hybrid of lexical and semantic) resolves its children
    here instead of importing their modules, so it picks up whatever is
    registered under that key in the running process.

    The child is built like :func:`resolve_strategy` builds the top-level
    strategy (:func:`build_strategy`). *config* is passed to a child's
    ``from_config`` hook; when omitted, a default :class:`SearchConfig` with
    ``search_mode=name`` is used. A composite usually forwards the
    *app_config* (and, where relevant, *config*) it received in its own
    ``from_config``. Do not resolve the composite's own key from inside its
    construction — that recurses.

    Raises ``SearchUnsupportedError`` when nothing is registered under
    ``(dialect, name)`` (including an unknown *dialect*), naming the modes
    that are registered for *dialect*, or when the child's ``from_config``
    rejects the config.
    """
    strategy_cls = _lookup(dialect, name)
    child_config = config if config is not None else SearchConfig(search_mode=name)
    return build_strategy(strategy_cls, child_config, app_config=app_config)


def _lookup(dialect: str, name: str) -> type[SearchStrategy]:
    strategy_cls = _STRATEGIES.get((dialect, name))
    if strategy_cls is None:
        available = sorted(m for (d, m) in _STRATEGIES if d == dialect)
        raise SearchUnsupportedError(
            f"No search strategy for ({dialect}, {name}). "
            f"Available modes for {dialect}: {available}"
        )
    return strategy_cls


def build_strategy(
    strategy_cls: type[SearchStrategy],
    config: SearchConfig,
    *,
    app_config: AppConfig | None = None,
) -> SearchStrategy:
    """Instantiate *strategy_cls* the way :func:`resolve_strategy` does.

    Calls the ``from_config`` hook when the class declares one
    (:class:`ConfigurableSearchStrategy`), otherwise the zero-arg constructor.
    """
    if isinstance(strategy_cls, ConfigurableSearchStrategy):
        return strategy_cls.from_config(config, app_config=app_config)
    return strategy_cls()
