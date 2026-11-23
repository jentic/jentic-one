"""Public application composition entrypoints.

Re-exports the composition-root builders from :mod:`jentic_one.__main__` under
stable, public names so downstream packages and architecture gates can build a
:class:`~jentic_one.shared.context.Context` and :class:`~fastapi.FastAPI` app
through the exact path ``jentic-one serve`` uses without importing private
``_``-prefixed symbols from ``__main__``.
"""

from __future__ import annotations

from jentic_one.__main__ import (
    _build_app as build_app,
)
from jentic_one.__main__ import (
    _build_context as build_context,
)
from jentic_one.__main__ import (
    _expand_allowed_dbs as expand_allowed_dbs,
)

__all__ = [
    "build_app",
    "build_context",
    "expand_allowed_dbs",
]
