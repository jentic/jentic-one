"""Public application composition and CLI entrypoints.

Re-exports the composition-root builders and CLI dispatcher from
:mod:`jentic_one.__main__` and :mod:`jentic_one.wiring` under stable, public
names so downstream packages and architecture gates can build a default
:class:`~jentic_one.shared.web.container.AppContainer`,
:class:`~jentic_one.shared.context.Context`, and :class:`~fastapi.FastAPI` app
or invoke the operator CLI without importing private ``_``-prefixed symbols
from ``__main__``.
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
from jentic_one.__main__ import (
    main as run_cli,
)
from jentic_one.wiring import build_default_container

__all__ = [
    "build_app",
    "build_context",
    "build_default_container",
    "expand_allowed_dbs",
    "run_cli",
]
