"""The ``rev_<uuid.hex>`` label a ``Jentic-Revision`` pin resolves through.

The broker's header parser (``broker/core/revisions.py``) encodes every pinned
revision as this label, and the registry resolver
(``registry/services/inspect/registry_service.py``) decodes it back to the
``ApiRevision.id``. Both halves live here, in the arch-neutral seam package, so
the two surfaces can never disagree on the encoding.
"""

from __future__ import annotations

import re
import uuid

REV_LABEL_PREFIX = "rev_"
_HEX32 = re.compile(r"[0-9A-Fa-f]{32}")


def encode_rev_label(revision_id: uuid.UUID) -> str:
    """The ``rev_<uuid.hex>`` label for ``revision_id``."""
    return f"{REV_LABEL_PREFIX}{revision_id.hex}"


def decode_rev_label(label: str) -> uuid.UUID | None:
    """The ``ApiRevision.id`` a ``rev_<uuid.hex>`` label names, or ``None`` if malformed.

    Only the form :func:`encode_rev_label` produces (``rev_`` + 32 hex digits, either
    case) decodes. ``uuid.UUID`` alone would also accept braces, hyphens and a
    ``urn:uuid:`` prefix, so the hex is checked first.
    """
    if not label.startswith(REV_LABEL_PREFIX):
        return None
    hex_part = label[len(REV_LABEL_PREFIX) :]
    if _HEX32.fullmatch(hex_part) is None:
        return None
    return uuid.UUID(hex=hex_part)
