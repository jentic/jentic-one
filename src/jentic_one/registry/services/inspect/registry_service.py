"""In-process URL → operation + API-identity resolver.

``RegistryService`` is the Registry surface's implementation of the broker's
``RegistryResolverProtocol``: it resolves a METHOD+URL to an operation plus the
API identity (``vendor/name/version``) that the bare URL lookup does not return,
reading the Registry DB in-process.

The broker depends only on the protocol (``shared/broker/protocols.py``) and
receives a concrete instance via dependency injection at app startup, so it never
imports ``jentic_one.registry``. A future ``RegistryServiceViaHttp`` could satisfy
the same protocol without any broker change.
"""

from __future__ import annotations

import uuid
from typing import Any

from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    ResolveResult,
    RevisionPinOutcome,
    RevisionPinResult,
)
from jentic_one.shared.models import ApiRevisionState

# A ``Jentic-Revision`` value carries the revision id as ``rev_<uuid.hex>`` (the
# 32-char hyphen-free hex form, which is what the spec ``rev_[A-Za-z0-9]+``
# pattern admits). Stripping the prefix yields the ``ApiRevision.id`` hex.
_REV_LABEL_PREFIX = "rev_"


class RegistryService:
    """In-process URL → operation + API-identity resolver (reads the Registry DB)."""

    def __init__(self, session: Any) -> None:  # AsyncSession
        self._session = session

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None:
        """Resolve a METHOD+URL to an operation and its API identity.

        Returns ``None`` when no operation matches the URL. May raise the
        registry lookup errors (``MethodNotAllowedError``, ``AmbiguousMatchError``,
        ``TooManyCandidatesError``) for the same conditions as ``URLLookupService``.
        """
        hit = await URLLookupService(self._session).resolve(
            method=method, url=url, revision_id=revision_id
        )
        if hit is None:
            return None
        api = await UrlIndexRepository.get_api_reference_for_operation(
            self._session, hit.operation_id
        )
        if api is None:
            return None
        return ResolveResult(
            operation_id=hit.operation_id,
            api=api,
            path_params=hit.path_params,
        )

    async def resolve_revision_pin(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        rev_label: str,
        identity: Identity,
    ) -> RevisionPinResult:
        """Translate a ``vendor:name:version=rev_…`` pin to a ``revision_id``.

        Reads the Registry DB in-process and classifies the pin into a neutral
        :class:`RevisionPinResult` (no registry exceptions cross the boundary):

        - unknown API / malformed label / no such revision → ``UNKNOWN`` (→ 422),
        - ``archived`` revision → ``ARCHIVED`` (→ 422; resurrect by re-promoting),
        - unpublished ``draft`` not owned by the caller → ``FORBIDDEN`` (→ 403),
        - ``published`` revision, or an owned ``draft`` → ``RESOLVED``.

        Ownership of a ``draft`` is decided by ``submitted_by == identity.sub`` —
        the only ownership signal the revision carries today.
        """
        revision_id = _parse_rev_label(rev_label)
        if revision_id is None:
            return RevisionPinResult(RevisionPinOutcome.UNKNOWN)

        api = await ApiRepository.get_by_identifier(self._session, vendor, name, version)
        if api is None:
            return RevisionPinResult(RevisionPinOutcome.UNKNOWN)

        revision = await ApiRevisionRepository.get_for_api(self._session, api.id, revision_id)
        if revision is None:
            return RevisionPinResult(RevisionPinOutcome.UNKNOWN)

        if revision.state == ApiRevisionState.ARCHIVED:
            return RevisionPinResult(RevisionPinOutcome.ARCHIVED)

        if revision.state == ApiRevisionState.DRAFT and revision.submitted_by != identity.sub:
            return RevisionPinResult(RevisionPinOutcome.FORBIDDEN)

        return RevisionPinResult(RevisionPinOutcome.RESOLVED, revision_id=revision_id)


def _parse_rev_label(rev_label: str) -> uuid.UUID | None:
    """Decode a ``rev_<uuid.hex>`` label to its ``ApiRevision.id`` (``None`` if invalid)."""
    if not rev_label.startswith(_REV_LABEL_PREFIX):
        return None
    raw = rev_label[len(_REV_LABEL_PREFIX) :]
    try:
        return uuid.UUID(hex=raw)
    except ValueError:
        return None
