"""In-process operation discovery over the injected ``RegistryResolverProtocol``.

A thin **service** (00-overview layering) over the resolver injected onto
``app.state.broker_registry_resolver`` at startup (``wiring.install_broker_registry_resolver``).
No ``httpx``, no ``registry_base_url``, no service token, and — critically — no
``jentic_one.registry`` import: the resolver owns its own registry-DB session
lifecycle, so discovery is an in-process DB read behind a protocol.
"""

from __future__ import annotations

import uuid

from jentic_one.broker.core.exceptions import (
    InvalidRevisionPinError,
    UnauthorizedRevisionPinError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    RegistryResolverProtocol,
    ResolveResult,
    RevisionPinOutcome,
)
from jentic_one.shared.schemas import APIReference


async def discover(
    resolver: RegistryResolverProtocol,
    *,
    method: str,
    url: str,
    revision_id: uuid.UUID | None = None,
) -> ResolveResult | None:
    """Resolve a METHOD+URL to an operation + API identity, or ``None`` if unmatched.

    Swapping in an HTTP-backed resolver later is a ``wiring.py`` change only.
    """
    return await resolver.resolve_operation(method=method, url=url, revision_id=revision_id)


async def resolve_pin_for_api(
    resolver: RegistryResolverProtocol,
    *,
    api: APIReference,
    pins: dict[tuple[str, str, str], str],
    identity: Identity,
) -> uuid.UUID | None:
    """Resolve the ``Jentic-Revision`` pin (if any) for a discovered API to a ``revision_id``.

    Looks up whether ``pins`` names the discovered ``(vendor, name, version)``; if
    not, returns ``None`` (resolve against ``current_revision_id`` as today). When
    a pin applies, translates the ``rev_…`` label in-process via the shared
    resolver and maps the neutral outcome to the broker taxonomy:

    - ``UNKNOWN`` / ``ARCHIVED`` → :class:`InvalidRevisionPinError` (422),
    - ``FORBIDDEN`` → :class:`UnauthorizedRevisionPinError` (403).

    The API identity uses the *discovered* ``vendor/name/version`` so a pin keyed
    on the same triple matches regardless of display-name fallbacks.
    """
    rev_label = pins.get((api.vendor, api.name, api.version))
    if rev_label is None:
        return None

    pin_key = f"{api.vendor}:{api.name}:{api.version}={rev_label}"
    result = await resolver.resolve_revision_pin(
        vendor=api.vendor,
        name=api.name,
        version=api.version,
        rev_label=rev_label,
        identity=identity,
    )

    if result.outcome is RevisionPinOutcome.RESOLVED and result.revision_id is not None:
        return result.revision_id
    if result.outcome is RevisionPinOutcome.FORBIDDEN:
        raise UnauthorizedRevisionPinError(
            detail=f"Not authorised to pin draft revision: {pin_key}",
            type="unauthorized_revision_pin",
        )
    if result.outcome is RevisionPinOutcome.ARCHIVED:
        raise InvalidRevisionPinError(
            detail=f"Cannot pin archived revision: {pin_key}",
            type="archived_revision_pin",
        )
    raise InvalidRevisionPinError(
        detail=f"Unknown revision pin: {pin_key}",
        type="unknown_revision_pin",
    )


async def discover_via_pins(
    resolver: RegistryResolverProtocol,
    *,
    method: str,
    url: str,
    pins: dict[tuple[str, str, str], str],
    identity: Identity,
) -> tuple[ResolveResult, uuid.UUID] | None:
    """Resolve a URL that no live revision serves through the request's own pins.

    Unpinned discovery only consults each API's live revision, so a URL defined
    solely by a draft (e.g. the first revision of an API, not yet promoted) is
    unmatched there. When the caller pins revisions, each pin already names its
    API, so try the pinned revisions directly: the first one that serves the URL
    wins. Pins the caller may not use (unknown, archived, another user's draft)
    are skipped rather than raised — they did not match this URL either way, so
    the caller gets the ordinary ``operation_not_found``.
    """
    for vendor, name, version in sorted(pins):
        api = APIReference(vendor=vendor, name=name, version=version)
        try:
            revision_id = await resolve_pin_for_api(resolver, api=api, pins=pins, identity=identity)
        except (InvalidRevisionPinError, UnauthorizedRevisionPinError):
            continue
        if revision_id is None:
            continue
        resolved = await discover(resolver, method=method, url=url, revision_id=revision_id)
        if resolved is not None:
            return resolved, revision_id
    return None
