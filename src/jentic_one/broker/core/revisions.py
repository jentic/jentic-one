"""Pure parsing of the multi-valued ``Jentic-Revision`` pin header.

Domain layer (00-overview): no FastAPI/httpx/DB imports. The header is parsed at
the web edge *before* any registry lookup so a malformed value is rejected with a
clean, agent-actionable ``InvalidRevisionPinError`` (→ 422 problem+json) rather
than surfacing as an uncaught ``500`` mid-lookup.

``_PIN`` is kept byte-for-byte in sync with the ``pattern`` on the
``JenticRevision`` parameter in ``openapi/broker/broker.openapi.yaml`` — an
arch/contract test (``tests/arch/test_revision_pin_regex_matches_spec.py``)
guards the drift.
"""

from __future__ import annotations

import re
import uuid

from jentic_one.broker.core.exceptions import InvalidRevisionPinError

# Mirrors the ``pattern`` on the ``JenticRevision`` parameter in
# ``openapi/broker/broker.openapi.yaml``. Each value is
# ``vendor:name:version=<revision>``, where ``<revision>`` is either the
# ``revision_id`` UUID every public response returns, or the equivalent
# ``rev_<uuid.hex>`` label (#1720).
_PIN = re.compile(
    r"^[a-z0-9-]+:[^=:]+:[^=]+="
    r"(?:rev_[A-Za-z0-9]+"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$"
)

_REV_LABEL_PREFIX = "rev_"

_EXPECTED_FORM = (
    "expected vendor:name:version=<revision_id>, with the revision_id from "
    "GET /apis/{vendor}/{name}/{version}/revisions"
)


def parse_revisions(header: str | None) -> dict[tuple[str, str, str], str]:
    """Parse the comma-separated ``Jentic-Revision`` header into a pin map.

    Returns a mapping of ``(vendor, name, version)`` → ``rev_…`` label. A value
    given as the ``revision_id`` UUID is normalised to its ``rev_<uuid.hex>``
    label, so every downstream consumer sees one form. An empty or absent header
    yields an empty map (no pins). Every non-empty value is validated against
    ``_PIN`` first; a value that fails raises :class:`InvalidRevisionPinError`
    (→ 422) **before** any registry lookup, so a naive ``split(":")`` can never
    raise a ``ValueError``/``500`` downstream.
    """
    out: dict[tuple[str, str, str], str] = {}
    for raw in (header or "").split(","):
        part = raw.strip()
        if not part:
            continue
        if not _PIN.match(part):
            raise InvalidRevisionPinError(
                detail=f"Malformed Jentic-Revision value: {part!r} ({_EXPECTED_FORM})",
                type="invalid_revision_pin",
            )
        api, _, rev = part.partition("=")
        # ``maxsplit=2`` keeps any colons inside the version segment (the spec's
        # ``[^=]+`` permits them, e.g. ``2023-10-16`` or a build-tagged semver) so
        # this can never raise ``ValueError``; the regex already guaranteed at
        # least two leading colons.
        vendor, name, version = api.split(":", 2)
        out[(vendor, name, version)] = _rev_label(rev.strip())
    return out


def _rev_label(revision: str) -> str:
    """The ``rev_…`` label for a pinned revision given as a label or a UUID.

    ``_PIN`` admits exactly these two forms, so anything without the ``rev_``
    prefix is a canonical hyphenated UUID and ``uuid.UUID`` cannot fail on it.
    """
    if revision.startswith(_REV_LABEL_PREFIX):
        return revision
    return f"{_REV_LABEL_PREFIX}{uuid.UUID(revision).hex}"
