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

from jentic_one.registry.core.url_index import expand_path_template
from jentic_one.registry.ingest.host_change_guard import (
    may_approve_host_change,
    pending_host_change,
)
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    DraftRevisionMatch,
    ResolveResult,
    RevisionPinOutcome,
    RevisionPinResult,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.schemas import APIReference, OperationInfo

# A ``Jentic-Revision`` value carries the revision id as ``rev_<uuid.hex>`` (the
# 32-char hyphen-free hex form, which is what the spec ``rev_[A-Za-z0-9]+``
# pattern admits). Stripping the prefix yields the ``ApiRevision.id`` hex.
_REV_LABEL_PREFIX = "rev_"


class RegistryService:
    """In-process URL → operation + API-identity resolver (reads the Registry DB)."""

    def __init__(self, session: Any, *, ctx: Context | None = None) -> None:  # AsyncSession
        self._session = session
        # Reaches the control/admin DBs for the server-host change guard on pinned
        # drafts; without it a host-changing draft counts as credential-bound.
        self._ctx = ctx

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
        op_ctx = hit.context
        return ResolveResult(
            operation=OperationInfo(
                id=hit.operation_id,
                path=op_ctx.path,
                method=op_ctx.method,
                relative_path=(
                    expand_path_template(op_ctx.path, hit.path_params) if op_ctx.path else None
                ),
            ),
            api=op_ctx.api,
            path_params=hit.path_params,
            server_variables=hit.server_variables,
            server_variable_defaults=hit.server_variable_defaults,
            server_variables_unresolved=hit.server_variables_unresolved,
        )

    async def find_draft_match(
        self, *, method: str, url: str, identity: Identity
    ) -> DraftRevisionMatch | None:
        """Name the best ``draft`` revision serving METHOD+URL that the caller may know of.

        Candidates come best first from
        :meth:`URLLookupService.find_draft_candidates`. Each is classified with
        :meth:`draft_pin_outcome` — the rule :meth:`resolve_revision_pin`
        applies — and the first one the caller submitted, or whose pin would
        resolve for it, is returned with that outcome. Any other principal's
        draft is passed over, so its existence is never disclosed.
        """
        candidates = await URLLookupService(self._session).find_draft_candidates(
            method=method, url=url
        )
        for candidate in candidates:
            outcome = await self.draft_pin_outcome(
                api_id=candidate.api_id,
                revision_id=candidate.row.revision_id,
                origin=candidate.origin,
                submitted_by=candidate.submitted_by,
                vendor=candidate.vendor,
                name=candidate.name,
                version=candidate.version,
                identity=identity,
            )
            if outcome is not RevisionPinOutcome.RESOLVED and not _submitted_by_caller(
                candidate.submitted_by, identity
            ):
                continue
            return DraftRevisionMatch(
                api=APIReference(
                    vendor=candidate.vendor, name=candidate.name, version=candidate.version
                ),
                revision_id=candidate.row.revision_id,
                pin_outcome=outcome,
            )
        return None

    async def draft_pin_outcome(
        self,
        *,
        api_id: uuid.UUID,
        revision_id: uuid.UUID,
        origin: str | None,
        submitted_by: str | None,
        vendor: str,
        name: str,
        version: str,
        identity: Identity,
    ) -> RevisionPinOutcome:
        """Whether ``identity`` may pin a ``draft`` revision (``Jentic-Revision``).

        The single pin rule for drafts, shared by :meth:`resolve_revision_pin`
        and :meth:`find_draft_match`:

        - a draft with an ``origin`` is an import held for server-host review
          (``registry/ingest/host_change_guard.py``): only an operator who could
          promote it may route through it, not the agent that submitted it —
          ``RESOLVED`` with ``credentials:write``, else ``FORBIDDEN``;
        - any other draft is pinnable only by its submitter (``FORBIDDEN``
          otherwise), and only while its server hosts need no operator: when
          they differ from the API's live ones on an API with bound
          credentials, a submitter without ``credentials:write`` gets
          ``HOST_CHANGE_HELD``.
        """
        if origin is not None:
            if may_approve_host_change(identity.permissions):
                return RevisionPinOutcome.RESOLVED
            return RevisionPinOutcome.FORBIDDEN
        if not _submitted_by_caller(submitted_by, identity):
            return RevisionPinOutcome.FORBIDDEN
        if may_approve_host_change(identity.permissions):
            return RevisionPinOutcome.RESOLVED
        held = await pending_host_change(
            self._ctx,
            self._session,
            api_id=api_id,
            revision_id=revision_id,
            vendor=vendor,
            name=name,
            version=version,
        )
        if held is not None:
            return RevisionPinOutcome.HOST_CHANGE_HELD
        return RevisionPinOutcome.RESOLVED

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
        - ``draft`` held for server-host review, for a caller without
          ``credentials:write`` (even its submitter) → ``FORBIDDEN`` (→ 403),
        - owned ``draft`` whose server hosts differ from the API's live ones, on
          an API with bound credentials, for a caller without
          ``credentials:write`` → ``HOST_CHANGE_HELD`` (→ 403): pinning it would
          send those credentials to hosts no operator approved, which
          promoting it would refuse (``registry/ingest/host_change_guard.py``),
        - ``published`` revision, or an owned ``draft`` → ``RESOLVED``.

        The ``draft`` arms are :meth:`draft_pin_outcome`, which also decides
        what the unregistered-URL hint (:meth:`find_draft_match`) offers.
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

        if revision.state == ApiRevisionState.DRAFT:
            outcome = await self.draft_pin_outcome(
                api_id=api.id,
                revision_id=revision_id,
                origin=revision.origin,
                submitted_by=revision.submitted_by,
                vendor=vendor,
                name=name,
                version=version,
                identity=identity,
            )
            if outcome is RevisionPinOutcome.FORBIDDEN:
                return RevisionPinResult(outcome)
            if outcome is RevisionPinOutcome.HOST_CHANGE_HELD:
                return RevisionPinResult(outcome, revision_id=revision_id)

        return RevisionPinResult(RevisionPinOutcome.RESOLVED, revision_id=revision_id)


def _submitted_by_caller(submitted_by: str | None, identity: Identity) -> bool:
    """Draft ownership: ``submitted_by == identity.sub``, the only signal a revision carries."""
    return submitted_by is not None and submitted_by == identity.sub


def _parse_rev_label(rev_label: str) -> uuid.UUID | None:
    """Decode a ``rev_<uuid.hex>`` label to its ``ApiRevision.id`` (``None`` if invalid)."""
    if not rev_label.startswith(_REV_LABEL_PREFIX):
        return None
    raw = rev_label[len(_REV_LABEL_PREFIX) :]
    try:
        return uuid.UUID(hex=raw)
    except ValueError:
        return None
