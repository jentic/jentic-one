"""In-process operation discovery over the injected ``RegistryResolverProtocol``.

A thin **service** (00-overview layering) over the resolver injected onto
``app.state.broker_registry_resolver`` at startup (``wiring.install_broker_registry_resolver``).
No ``httpx``, no ``registry_base_url``, no service token, and — critically — no
``jentic_one.registry`` import: the resolver owns its own registry-DB session
lifecycle, so discovery is an in-process DB read behind a protocol.
"""

from __future__ import annotations

import uuid

import structlog

from jentic_one.broker.core.exceptions import (
    InvalidRevisionPinError,
    OperationNotFoundError,
    UnauthorizedRevisionPinError,
    draft_revision_directive,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import APIS_READ, CREDENTIALS_WRITE
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.broker.protocols import (
    DraftRevisionMatch,
    RegistryResolverProtocol,
    ResolveResult,
    RevisionPinOutcome,
)
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.schemas import APIReference

logger = structlog.get_logger(__name__)

#: ``detail`` of the unregistered-URL 404 when nothing (not even a draft the
#: caller may see) serves the URL.
UNREGISTERED_URL_DETAIL = "Operation not found — unregistered upstream URL."


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
    - ``FORBIDDEN`` → :class:`UnauthorizedRevisionPinError` (403),
    - ``HOST_CHANGE_HELD`` → :class:`UnauthorizedRevisionPinError` (403
      ``host_change_requires_operator``).

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
    """
    for vendor, name, version in sorted(pins):
        rev_label = pins[(vendor, name, version)]
        result = await resolver.resolve_revision_pin(
            vendor=vendor, name=name, version=version, rev_label=rev_label, identity=identity
        )
        if result.revision_id is None or result.outcome not in (
            RevisionPinOutcome.RESOLVED,
            RevisionPinOutcome.HOST_CHANGE_HELD,
        ):
            continue
        resolved = await discover(resolver, method=method, url=url, revision_id=result.revision_id)
        if resolved is None:
            continue
        if result.outcome is RevisionPinOutcome.HOST_CHANGE_HELD:
            raise _host_change_held(f"{vendor}:{name}:{version}={rev_label}")
        return resolved, result.revision_id
    return None


async def unregistered_operation_error(
    resolver: RegistryResolverProtocol,
    *,
    method: str,
    url: str,
    identity: Identity,
) -> OperationNotFoundError:
    """The ``operation_not_found`` 404 for a METHOD+URL no live revision serves.

    Runs only once discovery has missed, so a matched call never pays for it.
    When the caller may read the registry's revisions (``apis:read`` — the
    permission ``GET /apis/{vendor}/{name}/{version}/revisions`` requires, which
    lists drafts too), one bounded registry statement looks for a ``draft``
    revision that serves the URL. A hit keeps the status and problem ``type``
    — client recovery keys on them — and names the draft in ``detail``, the
    ``draft_revision`` extension member and an ``agent_directive``. Without
    ``apis:read`` nothing is looked up and the generic 404 is returned. The
    lookup is best-effort: if it fails, the generic 404 is returned.
    """
    generic = OperationNotFoundError(detail=UNREGISTERED_URL_DETAIL, type="operation_not_found")
    if not has_effective_permission(identity.permissions, APIS_READ):
        logger.debug("draft_match_lookup_skipped", reason="caller_lacks_apis_read")
        return generic
    try:
        match = await resolver.find_draft_match(method=method, url=url)
    except Exception:
        logger.warning("draft_match_lookup_failed", method=method, exc_info=True)
        return generic
    if match is None:
        return generic
    logger.debug(
        "operation_not_found_draft_match",
        vendor=match.api.vendor,
        name=match.api.name,
        version=match.api.version,
        revision_id=str(match.revision_id),
    )
    return _draft_match_error(match, identity=identity)


def _draft_match_error(match: DraftRevisionMatch, *, identity: Identity) -> OperationNotFoundError:
    vendor, name, version = match.api.vendor, match.api.name, match.api.version
    revision_id = str(match.revision_id)
    promote_path = f"/apis/{vendor}/{name}/{version}/revisions/{revision_id}:promote"
    pin_header = (
        f"{vendor}:{name}:{version}=rev_{match.revision_id.hex}"
        if _may_pin(match, identity=identity)
        else None
    )
    return OperationNotFoundError(
        detail=(
            f"Operation not found — no live revision serves this URL; it matches revision "
            f"{revision_id} of API '{vendor}/{name}/{version}', which is a draft and has "
            "not been promoted"
        ),
        type="operation_not_found",
        extra={
            "draft_revision": {
                "api": {"vendor": vendor, "name": name, "version": version},
                "revision_id": revision_id,
                "state": ApiRevisionState.DRAFT.value,
                "promoted": False,
                "promote_path": promote_path,
            }
        },
        directive=draft_revision_directive(
            vendor=vendor,
            name=name,
            version=version,
            revision_id=revision_id,
            promote_path=promote_path,
            pin_header=pin_header,
        ),
    )


def _may_pin(match: DraftRevisionMatch, *, identity: Identity) -> bool:
    """Whether a ``Jentic-Revision`` pin to the draft would be accepted for the caller.

    Mirrors the registry's pin rule (``RegistryService.resolve_revision_pin``):
    a draft held for server-host review (it carries an ``origin``) is pinnable
    only with ``credentials:write``; any other draft only by its submitter. A
    submitter's draft whose hosts still need an operator answers its own 403
    (``host_change_requires_operator``) when pinned.
    """
    if match.origin is not None:
        return has_effective_permission(identity.permissions, CREDENTIALS_WRITE)
    return match.submitted_by is not None and match.submitted_by == identity.sub
