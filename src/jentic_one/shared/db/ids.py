"""Backend-neutral identifier generation.

Produces identifiers in Python — backend-neutral, with no dependency on
Postgres-specific server-side functions. The KSUID format matches the Postgres ``generate_ksuid``
SQL function byte-for-byte so values stay compatible across backends:

    ``<prefix>_<8-hex-second-timestamp><16-hex-random>``

where the timestamp is the lower 32 bits of the Unix epoch in seconds (8 hex
chars) and the random suffix is the first 16 hex chars of a random UUID.
"""

from __future__ import annotations

import time
import uuid


def generate_ksuid(prefix: str) -> str:
    """Generate a prefixed K-sortable identifier.

    Mirrors the Postgres ``generate_ksuid(prefix)`` SQL function:
    ``lpad(to_hex(epoch::bigint), 8, '0')`` followed by the first 16 hex
    characters of a random UUID.
    """
    epoch_seconds = int(time.time())
    timestamp_hex = format(epoch_seconds, "08x")[-8:]
    random_hex = uuid.uuid4().hex[:16]
    return f"{prefix}_{timestamp_hex}{random_hex}"


def new_uuid() -> uuid.UUID:
    """Generate a random UUID (matches Postgres ``gen_random_uuid``)."""
    return uuid.uuid4()
