"""In-process operation discovery over the injected ``RegistryResolverProtocol``.

A thin **service** over the resolver injected onto
``app.state.broker_registry_resolver`` at startup (``wiring.install_broker_registry_resolver``).
No ``httpx``, no ``registry_base_url``, no service token, and — critically — no
``jentic_one.registry`` import: the resolver owns its own registry-DB session
lifecycle, so discovery is an in-process DB read behind a protocol.
"""

from __future__ import annotations

import uuid

from jentic_one.broker.core.errors import (
    InvalidRevisionPinError,
    UnauthorizedRevisionPinError,
)
from jentic_one.broker.core.revisions import rev_label
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    RegistryResolverProtocol,
    ResolveResult,
    RevisionPinOutcome,
)
from jentic_one.shared.discovery.errors import MethodNotAllowedError
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
    a pin applies, translates it to its ``rev_…`` label, resolves that in-process
    via the shared resolver and maps the neutral outcome to the broker taxonomy:

    - ``UNKNOWN`` / ``ARCHIVED`` → :class:`InvalidRevisionPinError` (422),
    - ``FORBIDDEN`` → :class:`UnauthorizedRevisionPinError` (403),
    - ``HOST_CHANGE_HELD`` → :class:`UnauthorizedRevisionPinError` (403
      ``host_change_requires_operator``).

    Each error detail names the pin as the caller wrote it (``pins`` keeps the
    caller's spelling), so an agent that sent a ``revision_id`` finds that same
    value in the error.

    The API identity uses the *discovered* ``vendor/name/version`` so a pin keyed
    on the same triple matches regardless of display-name fallbacks.
    """
    revision = pins.get((api.vendor, api.name, api.version))
    if revision is None:
        return None

    pin_key = _pin_key(api.vendor, api.name, api.version, revision)
    result = await resolver.resolve_revision_pin(
        vendor=api.vendor,
        name=api.name,
        version=api.version,
        rev_label=rev_label(revision),
        identity=identity,
    )

    if result.outcome is RevisionPinOutcome.RESOLVED and result.revision_id is not None:
        return result.revision_id
    if result.outcome is RevisionPinOutcome.HOST_CHANGE_HELD:
        raise _host_change_held(pin_key)
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


def _pin_key(vendor: str, name: str, version: str, revision: str) -> str:
    """The pin as the caller wrote it in ``Jentic-Revision``, for error details."""
    return f"{vendor}:{name}:{version}={revision}"


def _host_change_held(pin_key: str) -> UnauthorizedRevisionPinError:
    return UnauthorizedRevisionPinError(
        detail=(
            f"Pinned draft revision {pin_key} serves different server hosts than the "
            "API's live revision, and the API has bound credentials; an operator "
            "holding 'credentials:write' must promote or pin it."
        ),
        type="host_change_requires_operator",
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
    the caller gets the ordinary ``operation_not_found``. A held draft (its hosts
    changed on a credential-bound API) that serves the URL is refused with 403
    ``host_change_requires_operator``: routing through it would send the API's
    bound credentials to hosts no operator approved.

    A pinned revision that serves the URL only for other methods does not stop
    the search either: a later pin may serve the requested method. Only when no
    pin resolves the call does :class:`MethodNotAllowedError` (405) surface,
    naming every method any pinned revision serves the URL for.
    """
    allowed_methods: list[str] = []
    for vendor, name, version in sorted(pins):
        revision = pins[(vendor, name, version)]
        result = await resolver.resolve_revision_pin(
            vendor=vendor,
            name=name,
            version=version,
            rev_label=rev_label(revision),
            identity=identity,
        )
        if result.revision_id is None or result.outcome not in (
            RevisionPinOutcome.RESOLVED,
            RevisionPinOutcome.HOST_CHANGE_HELD,
        ):
            continue
        try:
            resolved = await discover(
                resolver, method=method, url=url, revision_id=result.revision_id
            )
        except MethodNotAllowedError as exc:
            allowed_methods.extend(m for m in exc.allowed_methods if m not in allowed_methods)
            continue
        if resolved is None:
            continue
        if result.outcome is RevisionPinOutcome.HOST_CHANGE_HELD:
            raise _host_change_held(_pin_key(vendor, name, version, revision))
        return resolved, result.revision_id
    if allowed_methods:
        raise MethodNotAllowedError(sorted(allowed_methods))
    return None
