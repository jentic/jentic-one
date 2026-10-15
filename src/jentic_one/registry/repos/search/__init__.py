"""Backend-dispatched search capability.

The :class:`SearchStrategy` protocol is the primary extension point for adding
new search modes.  Register strategies with :func:`register_strategy` and
resolve them at runtime with :func:`resolve_strategy`.
"""

from __future__ import annotations

import jentic_one.registry.repos.search.postgres_lexical as _pg_lexical  # noqa: F401
import jentic_one.registry.repos.search.sqlite_lexical as _sqlite_lexical  # noqa: F401
from jentic_one.registry.repos.search.errors import SearchUnsupportedError
from jentic_one.registry.repos.search.protocol import (
    ConfigurableSearchStrategy,
    SearchCursor,
    SearchHit,
    SearchStrategy,
)
from jentic_one.registry.repos.search.registry import (
    build_strategy,
    register_strategy,
    resolve_strategy,
)

__all__ = [
    "ConfigurableSearchStrategy",
    "SearchCursor",
    "SearchHit",
    "SearchStrategy",
    "SearchUnsupportedError",
    "build_strategy",
    "register_strategy",
    "resolve_strategy",
]
