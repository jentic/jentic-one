"""In-process operation discovery over the injected ``RegistryResolverProtocol``.

A thin **service** (00-overview layering) over the resolver injected onto
``app.state.broker_registry_resolver`` at startup (``wiring.install_broker_registry_resolver``).
No ``httpx``, no ``registry_base_url``, no service token, and — critically — no
``jentic_one.registry`` import: the resolver owns its own registry-DB session
lifecycle, so discovery is an in-process DB read behind a protocol.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from jentic_one.broker.core.exceptions import (
    InvalidRevisionPinError,
    UnauthorizedRevisionPinError,
)
from jentic_one.broker.core.revisions import rev_label
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    RegistryResolverProtocol,
    ResolveResult,
    RevisionPinOutcome,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference


@dataclass(frozen=True, slots=True)
class PinnedRevision:
    """A ``Jentic-Revision`` pin the caller may route through.

    ``pin_key`` is the pin as the caller wrote it, for error details.
    ``credential_owner_required`` means the revision's server hosts still need
    approval, so the call may only use a credential the caller owns
    (:func:`require_pinned_credential_owner`).
    """

    revision_id: uuid.UUID
    pin_key: str
    credential_owner_required: bool = False


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
) -> PinnedRevision | None:
    """Resolve the ``Jentic-Revision`` pin (if any) for a discovered API to a revision.

    Looks up whether ``pins`` names the discovered ``(vendor, name, version)``; if
    not, returns ``None`` (resolve against ``current_revision_id`` as today). When
    a pin applies, translates it to its ``rev_…`` label, resolves that in-process
    via the shared resolver and maps the neutral outcome to the broker taxonomy:

    - ``UNKNOWN`` / ``ARCHIVED`` → :class:`InvalidRevisionPinError` (422),
    - ``FORBIDDEN`` → :class:`UnauthorizedRevisionPinError` (403),
    - ``HOST_CHANGE_HELD`` → :class:`UnauthorizedRevisionPinError` (403
      ``host_change_requires_operator``).

    A resolved pin whose draft still needs server-host approval comes back with
    ``credential_owner_required`` set; the caller checks the selected credential
    with :func:`require_pinned_credential_owner` before using it.

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
        return PinnedRevision(
            result.revision_id, pin_key, credential_owner_required=result.credential_owner_required
        )
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
    # States the rule only: whether anyone has a bound credential or an open
    # session on the API is not the caller's to learn from this refusal.
    return UnauthorizedRevisionPinError(
        detail=(
            f"Pinned draft revision {pin_key} serves different server hosts than the "
            "API's live revision; routing through it requires 'org:admin', or "
            "'credentials:write' and a credential the caller owns."
        ),
        type="host_change_requires_operator",
    )


async def require_pinned_credential_owner(
    ctx: Context,
    *,
    pinned: PinnedRevision,
    identity: Identity,
    credential_owner: str | None,
) -> None:
    """Refuse a call through a pinned host-changing draft with a credential the caller doesn't own.

    A no-op unless ``pinned.credential_owner_required``. The caller owns the
    credential when it created it or, for an agent, when its owning user did
    (``parent_actor_id``, else read from the agent row). Anything else,
    including a credential with no recorded creator, raises 403
    ``host_change_requires_operator``: the credential's owner has not approved
    the new hosts.
    """
    if not pinned.credential_owner_required:
        return
    if credential_owner is not None:
        if credential_owner == identity.sub:
            return
        if identity.actor_type is ActorType.AGENT:
            owner = identity.parent_actor_id or await ActorStatusResolver(
                ctx.admin_db
            ).agent_owner_id(identity.sub)
            if owner is not None and credential_owner == owner:
                return
    raise _host_change_held(pinned.pin_key)


async def discover_via_pins(
    resolver: RegistryResolverProtocol,
    *,
    method: str,
    url: str,
    pins: dict[tuple[str, str, str], str],
    identity: Identity,
) -> tuple[ResolveResult, PinnedRevision] | None:
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
    bound credentials to hosts nobody approved.
    """
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
        resolved = await discover(resolver, method=method, url=url, revision_id=result.revision_id)
        if resolved is None:
            continue
        pin_key = _pin_key(vendor, name, version, revision)
        if result.outcome is RevisionPinOutcome.HOST_CHANGE_HELD:
            raise _host_change_held(pin_key)
        return resolved, PinnedRevision(
            result.revision_id, pin_key, credential_owner_required=result.credential_owner_required
        )
    return None
