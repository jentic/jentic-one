"""Pluggable database backends.

Each backend encapsulates dialect-specific engine wiring and capabilities.
Select one with :func:`get_backend` based on ``DatabaseConfig.backend``.
"""

from __future__ import annotations

from jentic_one.shared.db.backends.base import DatabaseBackend
from jentic_one.shared.db.backends.postgres import PostgresBackend
from jentic_one.shared.db.backends.registry import get_backend
from jentic_one.shared.db.backends.sqlite import (
    SqliteBackend,
    configure_sqlite_pragmas,
    enable_sqlite_foreign_keys,
    enable_sqlite_manual_begin,
)

__all__ = [
    "DatabaseBackend",
    "PostgresBackend",
    "SqliteBackend",
    "configure_sqlite_pragmas",
    "enable_sqlite_foreign_keys",
    "enable_sqlite_manual_begin",
    "get_backend",
]
