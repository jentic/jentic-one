"""The ``rev_<uuid.hex>`` label a ``Jentic-Revision`` pin resolves through.

The broker's header parser (``broker/core/revisions.py``) encodes every pinned
revision as this label, and the registry resolver
(``registry/services/inspect/registry_service.py``) decodes it back to the
``ApiRevision.id``. Both halves live here, in the arch-neutral seam package, so
the two surfaces can never disagree on the encoding.
"""

from __future__ import annotations

import uuid

REV_LABEL_PREFIX = "rev_"


def encode_rev_label(revision_id: uuid.UUID) -> str:
    """The ``rev_<uuid.hex>`` label for ``revision_id``."""
    return f"{REV_LABEL_PREFIX}{revision_id.hex}"


def decode_rev_label(label: str) -> uuid.UUID | None:
    """The ``ApiRevision.id`` a ``rev_<uuid.hex>`` label names, or ``None`` if malformed."""
    if not label.startswith(REV_LABEL_PREFIX):
        return None
    try:
        return uuid.UUID(hex=label[len(REV_LABEL_PREFIX) :])
    except ValueError:
        return None
