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

from jentic_one.broker.core.exceptions import InvalidRevisionPinError

# Mirrors the ``pattern`` on the ``JenticRevision`` parameter in
# ``openapi/broker/broker.openapi.yaml``. Each value is
# ``vendor:name:version=rev_…``; the ``rev_`` label carries the revision id.
_PIN = re.compile(r"^[a-z0-9-]+:[^=:]+:[^=]+=rev_[A-Za-z0-9]+$")


def parse_revisions(header: str | None) -> dict[tuple[str, str, str], str]:
    """Parse the comma-separated ``Jentic-Revision`` header into a pin map.

    Returns a mapping of ``(vendor, name, version)`` → ``rev_…`` label. An empty
    or absent header yields an empty map (no pins). Every non-empty value is
    validated against ``_PIN`` first; a value that fails raises
    :class:`InvalidRevisionPinError` (→ 422) **before** any registry lookup, so a
    naive ``split(":")`` can never raise a ``ValueError``/``500`` downstream.
    """
    out: dict[tuple[str, str, str], str] = {}
    for raw in (header or "").split(","):
        part = raw.strip()
        if not part:
            continue
        if not _PIN.match(part):
            raise InvalidRevisionPinError(
                detail=f"Malformed Jentic-Revision value: {part!r}",
                type="invalid_revision_pin",
            )
        api, _, rev = part.partition("=")
        # ``maxsplit=2`` keeps any colons inside the version segment (the spec's
        # ``[^=]+`` permits them, e.g. ``2023-10-16`` or a build-tagged semver) so
        # this can never raise ``ValueError``; the regex already guaranteed at
        # least two leading colons.
        vendor, name, version = api.split(":", 2)
        out[(vendor, name, version)] = rev.strip()
    return out
