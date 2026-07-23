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

from sqlalchemy import MetaData

import jentic_one.admin.core.schema  # registers admin models on AdminBase.metadata
import jentic_one.control.core.schema  # registers control models on ControlBase.metadata
import jentic_one.registry.core.schema  # noqa: F401  # registers registry models on RegistryBase.metadata
from jentic_one.shared.db.base import AdminBase, ControlBase, RegistryBase


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    """A single migration target: its metadata and its version-table name.

    ``version_table`` names the per-target ``alembic_version`` table. Because all
    targets share one Postgres instance (separated by schema), each must track its
    own revision head in its own version table (in its own schema) — otherwise two
    targets collide on a single ``alembic_version`` and clobber each other's head.
    The built-in targets keep the default (``alembic_version``, already scoped
    per-schema by ``env.py``'s ``version_table_schema``); the field only matters
    when a target needs a distinct version table within a shared schema.
    """

    name: str
    metadata: MetaData
    version_table: str = "alembic_version"


#: Ordered registry of migration targets, keyed by name. Insertion order is the
#: canonical UPGRADE order (rollback reverses it — see run.py).
DB_TARGETS: dict[str, MigrationTarget] = {}


def register_target(target: MigrationTarget) -> None:
    """Register a migration target. Idempotent for the same ``(name, target)``."""
    existing = DB_TARGETS.get(target.name)
    if existing is not None and existing != target:
        raise ValueError(f"Migration target {target.name!r} already registered")
    DB_TARGETS[target.name] = target


register_target(MigrationTarget("registry", RegistryBase.metadata))
register_target(MigrationTarget("control", ControlBase.metadata))
register_target(MigrationTarget("admin", AdminBase.metadata))


#: Backward-compatible name→metadata mapping. Derived from :data:`DB_TARGETS` so
#: any code (or test) still importing ``DB_METADATA`` keeps working. Prefer
#: ``DB_TARGETS`` in new code (it also carries ``version_table``).
DB_METADATA: dict[str, MetaData] = {name: t.metadata for name, t in DB_TARGETS.items()}
