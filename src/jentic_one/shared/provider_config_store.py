"""Boundary-safe reader for runtime provider configs stored in the admin DB.

The control module owns the credential ``ProviderRegistry`` but may not import
admin ORM models (enforced by ``tests/arch/test_module_boundaries.py``). This
helper exposes a raw, parameterized-SQL reader against the admin
``provider_configs`` table so the registry can pick up runtime configuration
without crossing the module boundary.

The returned dicts are the stored JSON payloads verbatim — any secret fields
(e.g. ``client_secret``) are still encrypted; decryption is the caller's job.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _decode(raw: Any) -> dict[str, Any]:
    """Decode a stored config_json column into a dict.

    Postgres ``JSON``/``JSONB`` returns a parsed object; SQLite stores JSON as
    text and returns a string. Handle both so the helper is dialect-agnostic.
    """
    if isinstance(raw, str):
        decoded: Any = json.loads(raw)
    else:
        decoded = raw
    if not isinstance(decoded, dict):
        raise ValueError("provider config_json must decode to an object")
    return decoded


async def load_provider_configs(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Return all runtime provider configs as ``{name: config_dict}``."""
    stmt = text("SELECT name, config_json FROM provider_configs")
    result = await session.execute(stmt)
    return {row.name: _decode(row.config_json) for row in result}
