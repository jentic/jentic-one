"""Migration target registry for all databases.

Replaces the old hardcoded ``DB_METADATA`` dict with an ordered registry of
:class:`MigrationTarget` records (name + metadata + version-table name) plus a
:func:`register_target` hook. The built-in ``registry``/``control``/``admin``
targets register at import; a downstream package can import this module and
append its own isolated target via ``register_target(MigrationTarget(...))``
without editing this file.

Insertion order is the canonical UPGRADE order; rollback reverses it (see
``run.py``). This registry is the single source of truth for migration sequencing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import MetaData

import jentic_one.admin.core.schema  # registers admin models on AdminBase.metadata
import jentic_one.control.core.schema  # registers control models on ControlBase.metadata
import jentic_one.registry.core.schema  # noqa: F401  # registers registry models on RegistryBase.metadata
from jentic_one.shared.db.base import AdminBase, ControlBase, RegistryBase


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    """A single migration target: its metadata, version tracking, and config routing.

    ``version_table`` names the per-target ``alembic_version`` table. Because all
    targets can share one Postgres instance (or one database schema), each target
    tracks its own revision head in its own version table — otherwise two targets
    collide on a single ``alembic_version`` and clobber each other's head. The
    built-in targets keep the default (``alembic_version``, scoped per-schema by
    ``env.py``'s ``version_table_schema``); an extension target sharing a schema
    or SQLite file sets a distinct ``version_table``.

    ``version_locations`` overrides the directory containing Alembic revision
    scripts. When ``None``, ``run.py`` resolves ``<migrations_dir>/<name>/versions``.

    ``db_section`` names the ``AppConfig.databases`` section from which ``env.py``
    resolves connection details (URL, dialect, default schema). When ``None``,
    ``env.py`` looks up ``target.name``. An extension sharing an existing database
    sets ``db_section`` (for example ``db_section="registry"``).

    ``version_table_schema`` overrides the PostgreSQL schema used for
    ``search_path``, schema bootstrap, reflection, and ``version_table_schema``
    in ``env.py``. When ``None``, ``env.py`` uses the ``schema_name`` of the
    resolved ``db_section``.
    """

    name: str
    metadata: MetaData
    version_table: str = "alembic_version"
    version_locations: Path | str | None = None
    db_section: str | None = None
    version_table_schema: str | None = None


_BUILTIN_TARGETS: frozenset[str] = frozenset({"registry", "control", "admin"})

#: Ordered registry of migration targets, keyed by name. Insertion order is the
#: canonical UPGRADE order (rollback reverses it — see run.py).
DB_TARGETS: dict[str, MigrationTarget] = {}

#: Backward-compatible name→metadata mapping kept in sync with :data:`DB_TARGETS`
#: so callers importing ``DB_METADATA`` observe targets registered after import.
#: Prefer ``DB_TARGETS`` in new code.
DB_METADATA: dict[str, MetaData] = {}


def register_target(target: MigrationTarget) -> None:
    """Register a migration target. Idempotent for the same ``(name, target)``."""
    existing = DB_TARGETS.get(target.name)
    if existing is not None and existing != target:
        raise ValueError(f"Migration target {target.name!r} already registered")
    DB_TARGETS[target.name] = target
    DB_METADATA[target.name] = target.metadata


def unregister_target(name: str) -> None:
    """Remove a non-built-in migration target from the registry (for test cleanup)."""
    if name in _BUILTIN_TARGETS:
        raise ValueError(f"Migration target {name!r} is built in and cannot be unregistered")
    DB_TARGETS.pop(name, None)
    DB_METADATA.pop(name, None)


register_target(MigrationTarget("registry", RegistryBase.metadata))
register_target(MigrationTarget("control", ControlBase.metadata))
register_target(MigrationTarget("admin", AdminBase.metadata))
