"""Backend-dispatched search capability.

The :class:`SearchStrategy` protocol is the primary extension point for adding
new search modes.  Register strategies with :func:`register_strategy` and
resolve them at runtime with :func:`resolve_strategy`.

Composite strategies resolve their children with
:func:`resolve_strategy_by_name` (never by importing a strategy module), and
every strategy applies the shared filter contract —
:data:`ACTIVE_REVISION_STATES` / :func:`search_filter_clause` — so results stay
filter-identical across strategies.
"""

from __future__ import annotations

import jentic_one.registry.repos.search.postgres_lexical as _pg_lexical  # noqa: F401
import jentic_one.registry.repos.search.sqlite_lexical as _sqlite_lexical  # noqa: F401
from jentic_one.registry.repos.search.errors import SearchUnsupportedError
from jentic_one.registry.repos.search.filters import (
    ACTIVE_REVISION_STATES,
    search_filter_clause,
)
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
    resolve_strategy_by_name,
)

__all__ = [
    "ACTIVE_REVISION_STATES",
    "ConfigurableSearchStrategy",
    "SearchCursor",
    "SearchHit",
    "SearchStrategy",
    "SearchUnsupportedError",
    "build_strategy",
    "register_strategy",
    "resolve_strategy",
    "resolve_strategy_by_name",
    "search_filter_clause",
]
