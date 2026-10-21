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
from jentic_one.shared.broker.revision_label import (
    REV_LABEL_PREFIX,
    decode_rev_label,
    encode_rev_label,
)

# Mirrors the ``pattern`` on the ``JenticRevision`` parameter in
# ``openapi/broker/broker.openapi.yaml``. Each value is
# ``vendor:name:version=<revision>``, where ``<revision>`` is either the
# ``revision_id`` UUID every public response returns, or the equivalent
# ``rev_<uuid.hex>`` label (#1720).
_PIN = re.compile(
    r"^[a-z0-9-]+:[^=:]+:[^=]+="
    r"(?:rev_[0-9A-Fa-f]{32}"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$"
)

_EXPECTED_FORM = (
    "expected vendor:name:version=<revision_id>, with the revision_id from "
    "GET /apis/{vendor}/{name}/{version}/revisions"
)


def parse_revisions(header: str | None) -> dict[tuple[str, str, str], str]:
    """Parse the comma-separated ``Jentic-Revision`` header into a pin map.

    Returns a mapping of ``(vendor, name, version)`` → the pinned revision exactly
    as the caller wrote it (the ``revision_id`` UUID or its ``rev_<uuid.hex>``
    label). Keeping the caller's spelling lets every pin error echo the value the
    caller sent; :func:`rev_label` gives the one form the resolver consumes. An
    empty or absent header yields an empty map (no pins). Every non-empty value
    is validated against ``_PIN`` first; a value that fails raises
    :class:`InvalidRevisionPinError` (→ 422) **before** any registry lookup, so a
    naive ``split(":")`` can never raise a ``ValueError``/``500`` downstream.

    The same API may be pinned more than once only to the same revision (in
    either spelling); two different revisions for one API are ambiguous and
    rejected with the same 422.
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
        key = (vendor, name, version)
        revision = rev.strip()
        earlier = out.get(key)
        if earlier is not None and rev_label(earlier) != rev_label(revision):
            raise InvalidRevisionPinError(
                detail=(
                    f"Conflicting Jentic-Revision values for {api}: {earlier!r} and "
                    f"{revision!r} (pin each API to one revision)"
                ),
                type="invalid_revision_pin",
            )
        out[key] = revision
    return out


def rev_label(revision: str) -> str:
    """The lowercase ``rev_<uuid.hex>`` label for a pinned revision.

    ``revision`` is a value from :func:`parse_revisions` — a ``rev_`` label or a
    canonical hyphenated UUID. A value in neither form is returned unchanged, so
    the resolver classifies it as an unknown pin rather than this raising.
    """
    if revision.startswith(REV_LABEL_PREFIX):
        decoded = decode_rev_label(revision)
        return revision if decoded is None else encode_rev_label(decoded)
    try:
        return encode_rev_label(uuid.UUID(revision))
    except ValueError:
        return revision
