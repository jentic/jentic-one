"""Stable keys for Postgres advisory locks, and the shared locks built on them.

``pg_advisory_lock`` / ``pg_try_advisory_lock`` take a signed 64-bit key.
Deriving it from a human-readable lock name keeps call sites self-describing
while staying collision-resistant across the whole key space. The hash is
computed in Python (BLAKE2b) rather than with ``hashtext()`` so the key is the
same on every Postgres version and is known before the query runs (an operator
can compute it to find the holder in ``pg_locks``: ``classid``/``objid`` are the
high/low 32 bits of the unsigned key).
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def advisory_lock_key(name: str) -> int:
    """Map a lock name to a stable signed 64-bit advisory-lock key.

    Deterministic across processes, hosts, and Python versions (unlike the
    builtin ``hash``, which is salted per process). Names should be namespaced
    (``"<module>.<purpose>"``) so unrelated features cannot pick the same one.
    """
    if not name:
        raise ValueError("advisory lock name must be non-empty")
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def lock_credential_bindings(session: AsyncSession, credential_id: str) -> None:
    """Serialise writes to one credential's ``agent_credential_bindings`` rows.

    Taken by every runtime path that creates a binding and by a check that
    must see a stable set of bound agents until its own transaction ends.
    Call it on the admin DB session, inside a transaction.

    On PostgreSQL this is ``pg_advisory_xact_lock`` (released at commit or
    rollback). The key comes from :func:`advisory_lock_key` over a
    namespaced name, not ``hashtext(credential_id)``, so it does not contend
    with the credential's refresh lock even when the admin and control
    schemas share one database. On
    SQLite it is a no-op: every write transaction opens with
    ``BEGIN IMMEDIATE``, which already serialises writers to the admin file.
    """
    dialect = session.bind.dialect.name if session.bind else "sqlite"
    if dialect == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": advisory_lock_key(f"credentials.bindings.{credential_id}")},
        )
