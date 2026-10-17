"""Stable keys for Postgres advisory locks.

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
