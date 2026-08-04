"""Overlay service — submission, retrieval, lifecycle transitions for overlays."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.spec_file_repo import SpecFileRepository
from jentic_one.registry.services.errors import (
    ApiNotFoundError,
    InvalidOverlayDocumentError,
    NoCurrentRevisionError,
    OverlayApplyConflictError,
    OverlayNotFoundError,
    OverlayRematerializeForbiddenError,
    OverlayRollbackTargetMissingError,
    OverlayStateConflictError,
    SpecFileMissingError,
)
from jentic_one.registry.services.overlay_apply import OverlayApplyError, apply_overlay
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.models import ORIGIN_OVERLAY, JobKind, OverlayStatus
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor
from jentic_one.shared.url_validation import validate_upstream_url

logger = structlog.get_logger(__name__)

#: Upper bound on the number of actions an overlay document may carry. An overlay is
#: a small, targeted fix (a handful of remove/update pairs); a document with thousands
#: of actions is abuse, not a legitimate fix, and would be unbounded work at apply time.
_MAX_OVERLAY_ACTIONS = 512

#: Upper bound on the serialized size of an overlay document accepted at submit/update.
#: The document is contributor-controlled JSONB that an operator later materializes
#: inline (bypassing the URL-fetch cap), so bound it at ingress. Smaller than
#: max_spec_bytes because an overlay is a diff, not a whole spec.
_MAX_OVERLAY_DOCUMENT_BYTES = 1 * 1024 * 1024  # 1 MB


def _validate_overlay_document(document: Any) -> None:
    """Reject a structurally-invalid or oversized overlay document at submit/update.

    Cheap ingress guard (see the two bounds above). Full JSONPath/action semantics are
    still validated at confirm time by the applier; this only stops obvious abuse and
    unbounded payloads from being persisted by a contributor (``apis:write``).
    """
    if not isinstance(document, dict):
        raise InvalidOverlayDocumentError("document must be a JSON object")
    actions = document.get("actions")
    if not isinstance(actions, list):
        raise InvalidOverlayDocumentError("document must have an 'actions' array")
    if len(actions) > _MAX_OVERLAY_ACTIONS:
        raise InvalidOverlayDocumentError(
            f"too many actions ({len(actions)} > {_MAX_OVERLAY_ACTIONS})"
        )
    try:
        size = len(json.dumps(document).encode())
    except (TypeError, ValueError) as exc:
        raise InvalidOverlayDocumentError(f"document is not JSON-serializable: {exc}") from exc
    if size > _MAX_OVERLAY_DOCUMENT_BYTES:
        raise InvalidOverlayDocumentError(f"document exceeds {_MAX_OVERLAY_DOCUMENT_BYTES} bytes")


def _iter_server_urls(spec: dict[str, Any]) -> Iterator[str]:
    """Yield every ``servers[].url`` string in a spec.

    OpenAPI allows a ``servers`` array at the document root, under each Path Item,
    and under each Operation. All three are real upstream targets, so all three must
    be SSRF-validated when an overlay rewrites the spec — not just the top-level one.
    """

    def _from_servers(node: Any) -> Iterator[str]:
        if isinstance(node, list):
            for server in node:
                if isinstance(server, dict) and isinstance(server.get("url"), str):
                    yield server["url"]

    yield from _from_servers(spec.get("servers"))

    paths = spec.get("paths")
    if isinstance(paths, dict):
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            yield from _from_servers(path_item.get("servers"))
            for operation in path_item.values():
                if isinstance(operation, dict):
                    yield from _from_servers(operation.get("servers"))


def _concrete_server_urls(url: str) -> list[str]:
    """Expand a (possibly templated) server URL into concrete URL(s) to validate.

    A non-templated URL yields itself. For an RFC 6570-style templated URL (e.g.
    ``https://{region}.example.com``) we cannot know the runtime value, so we return
    the literal parts around the variables joined so an attacker can't hide a blocked
    host purely inside a variable: we strip the ``{var}`` placeholders and validate the
    remaining literal skeleton. If the skeleton has no host left (whole host is a
    variable), we skip — those are validated at execution time against egress policy.
    """
    if "{" not in url:
        return [url]
    # Replace each {var} with a neutral placeholder label so the literal host parts
    # still parse; if a blocked host is spelled out literally around the variable
    # (e.g. "http://169.254.169.254/{path}") it is still caught.
    skeleton = re.sub(r"\{[^}]*\}", "x", url)
    parsed = urlparse(skeleton if "://" in skeleton else f"https://{skeleton}")
    # If the host is entirely made of substituted placeholders (e.g. "{host}"), there
    # is nothing literal to validate here; defer to execution-time validation.
    if not parsed.hostname or parsed.hostname == "x":
        return []
    return [skeleton]


@dataclass(frozen=True)
class OverlayView:
    """Resolved view of a single overlay with context for link construction."""

    id: str
    api_id: uuid.UUID
    vendor: str
    name: str
    version: str
    status: str
    document: dict[str, Any]
    target_revision_id: uuid.UUID | None
    #: The revision produced by materializing this overlay, set by the ingest job on a
    #: successful confirm. NULL until materialization completes (and for pending
    #: overlays). Distinct from ``target_revision_id`` (the base the overlay targets).
    confirmed_revision_id: uuid.UUID | None
    contributed_by: str | None
    confirmed_by_execution_id: str | None
    created_at: datetime
    updated_at: datetime | None
    confirmed_at: datetime | None
    deprecated_at: datetime | None


class OverlayPageItem(BaseModel):
    """View model for a single overlay in a paginated list."""

    id: str
    api_id: uuid.UUID
    status: str
    document: dict[str, Any]
    target_revision_id: uuid.UUID | None
    confirmed_revision_id: uuid.UUID | None
    contributed_by: str | None
    confirmed_by_execution_id: str | None
    created_at: datetime
    updated_at: datetime | None
    confirmed_at: datetime | None
    deprecated_at: datetime | None


class OverlayPage(BaseModel):
    """Paginated result of overlays."""

    data: list[OverlayPageItem]
    has_more: bool
    next_cursor: str | None = None


class OverlayService:
    """Operations for overlay lifecycle management."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def _resolve_api(self, vendor: str, name: str, version: str) -> Api:
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
        if api is None:
            raise ApiNotFoundError(vendor, name, version)
        return api

    async def _load_base_spec(
        self,
        vendor: str,
        name: str,
        version: str,
        overlay_id: str,
        target_revision_id: uuid.UUID | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Load the base revision's spec content + source_url + digest an overlay applies to.

        The base is the API's current (live) revision. Returns the parsed spec dict,
        its ``source_url`` (so the materialized revision keeps the same catalog
        provenance for Flow-3 update detection), and its ``spec_digest`` (persisted on
        the materialized revision as ``overlay_base_digest`` so the sweep can diff
        upstream against the overlay's base rather than the overlaid digest).

        NOTE: materialization always applies to the *current* revision, not to the
        overlay's ``target_revision_id`` (the base it was authored against). Structural
        drift is caught by the applier (unresolved targets → 409); a semantic drift
        (target still resolves) would apply silently, so we emit a warning when the
        overlay's target no longer matches the current revision. Per-target-revision
        materialization is a deferred follow-up (see the flywheel tracker).
        """
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier_with_current_revision(
                session, vendor, name, version
            )
            if api is None:
                raise ApiNotFoundError(vendor, name, version)
            if api.current_revision_id is None:
                raise NoCurrentRevisionError(vendor, name, version)
            if target_revision_id is not None and target_revision_id != api.current_revision_id:
                logger.warning(
                    "overlay_confirm_base_drift",
                    overlay_id=overlay_id,
                    target_revision_id=str(target_revision_id),
                    current_revision_id=str(api.current_revision_id),
                    detail=(
                        "overlay authored against a revision that is no longer current; "
                        "applying to current — verify the fix still applies as intended"
                    ),
                )
            spec_file = await SpecFileRepository.get_for_revision(session, api.current_revision_id)
            if spec_file is None:
                raise SpecFileMissingError(str(api.current_revision_id))
            source_url = api.current_revision.source_url if api.current_revision else None
            base_digest = api.current_revision.spec_digest if api.current_revision else None
            return dict(spec_file.content), source_url, base_digest

    async def _load_base_spec_for_revision(
        self,
        api_id: uuid.UUID,
        overlay_id: str,
        revision_id: uuid.UUID,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Load a *specific* revision's spec content + source_url + digest (D1).

        The re-materialize-on-edit path (:meth:`update`) must re-apply the edited overlay
        document over the overlay's **original pre-overlay base** — the revision it
        superseded when it was first confirmed — never over the overlay's own currently-
        served output (which would double-apply the patch). That base is the overlay's
        ``superseded_revision_id``; this loads it. Raises ``OverlayRollbackTargetMissingError``
        if the base revision or its spec file is gone (pruned), so the caller can surface a
        clear "resolve manually" conflict rather than materializing over nothing.
        """
        async with self._ctx.registry_db.session() as session:
            revision = await ApiRevisionRepository.get_for_api(session, api_id, revision_id)
            if revision is None:
                raise OverlayRollbackTargetMissingError(
                    overlay_id,
                    f"base revision '{revision_id}' to re-apply the edit over is no longer "
                    "present — resolve manually, e.g. by rolling back then re-confirming",
                )
            spec_file = await SpecFileRepository.get_for_revision(session, revision_id)
            if spec_file is None:
                raise OverlayRollbackTargetMissingError(
                    overlay_id,
                    f"spec file for base revision '{revision_id}' is missing — resolve manually",
                )
            return dict(spec_file.content), revision.source_url, revision.spec_digest

    def _apply_and_validate(
        self, overlay_id: str, base_content: dict[str, Any], document: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply the overlay to the base spec and validate the result, or raise."""
        try:
            overlaid = apply_overlay(base_content, document)
        except OverlayApplyError as exc:
            raise OverlayApplyConflictError(overlay_id, str(exc)) from exc

        if not isinstance(overlaid.get("openapi"), str):
            raise OverlayApplyConflictError(
                overlay_id, "overlaid spec is missing a string 'openapi' version"
            )

        # Bound the materialized spec: the overlay document is contributor-controlled
        # (apis:write) JSONB and an org:admin confirm re-ingests it inline, which
        # bypasses the URL-fetch size cap. Reject a spec that would exceed the ingest
        # max_spec_bytes so a malicious/oversized overlay can't DoS the worker/DB.
        max_bytes = self._ctx.config.ingest.max_spec_bytes
        if max_bytes and len(json.dumps(overlaid).encode()) > max_bytes:
            raise OverlayApplyConflictError(
                overlay_id, f"overlaid spec exceeds max_spec_bytes ({max_bytes})"
            )

        # SSRF guard: the overlay is the one place an operator can rewrite server URLs,
        # so re-validate every server URL (document-, path-, and operation-level)
        # against egress policy before we let it be served.
        egress = self._ctx.config.ingest.egress
        for url in _iter_server_urls(overlaid):
            for concrete in _concrete_server_urls(url):
                try:
                    validate_upstream_url(concrete, egress)
                except ValueError as exc:
                    raise OverlayApplyConflictError(
                        overlay_id, f"unsafe servers[].url rejected: {exc}"
                    ) from exc
        return overlaid

    async def _enqueue_materialize_job(
        self,
        vendor: str,
        name: str,
        version: str,
        *,
        overlay_id: str,
        overlaid_spec: dict[str, Any],
        base_source_url: str | None,
        base_digest: str | None,
        identity: Identity,
    ) -> None:
        """Enqueue the re-ingest that rewrites the served spec for a confirmed overlay."""
        source: dict[str, Any] = {
            "type": "inline",
            "content": json.dumps(overlaid_spec),
            "filename": "openapi.json",
            "vendor": vendor,
            "api_name": name,
            "version": version,
            "submitted_by": identity.sub,
            "origin": ORIGIN_OVERLAY,
            "source_url": base_source_url,
            "overlay_base_digest": base_digest,
            # Carried into the ingest spec so CreateRevisionStage can tell a re-materialize
            # of *this* overlay (keep its clean-base superseded pointer) from a stacked
            # confirm of a *different* overlay over a live overlay's output.
            "overlay_id": overlay_id,
        }
        async with self._ctx.admin_db.transaction() as session:
            await enqueue_job(
                session,
                JobKind.IMPORT,
                created_by=identity.sub,
                actor_type=identity.actor_type,
                payload={"sources": [source], "overlay_id": overlay_id},
            )

    async def _get_overlay_or_raise(
        self,
        api_id: uuid.UUID,
        overlay_id: str,
        vendor: str,
        name: str,
        version: str,
    ) -> Any:
        async with self._ctx.registry_db.session() as session:
            overlay = await OverlayRepository.get_for_api(session, api_id, overlay_id)
        if overlay is None:
            raise OverlayNotFoundError(overlay_id, vendor, name, version)
        return overlay

    def _view(self, vendor: str, name: str, version: str, overlay: Any) -> OverlayView:
        return OverlayView(
            id=overlay.id,
            api_id=overlay.api_id,
            vendor=vendor,
            name=name,
            version=version,
            status=overlay.status,
            document=overlay.document,
            target_revision_id=overlay.target_revision_id,
            confirmed_revision_id=overlay.confirmed_revision_id,
            contributed_by=overlay.contributed_by,
            confirmed_by_execution_id=overlay.confirmed_by_execution_id,
            created_at=overlay.created_at,
            updated_at=overlay.updated_at,
            confirmed_at=overlay.confirmed_at,
            deprecated_at=overlay.deprecated_at,
        )

    async def submit(
        self,
        vendor: str,
        name: str,
        version: str,
        document: dict[str, Any],
        target_revision_id: uuid.UUID | None = None,
        contributed_by: str | None = None,
        *,
        identity: Identity,
    ) -> OverlayView:
        _validate_overlay_document(document)
        api = await self._resolve_api(vendor, name, version)

        async with self._ctx.registry_db.transaction() as session:
            overlay = await OverlayRepository.create(
                session,
                api_id=api.id,
                document=document,
                target_revision_id=target_revision_id,
                contributed_by=contributed_by,
                created_by=identity.sub,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.OVERLAY,
            target_id=overlay.id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            origin=identity.origin.value,
        )
        return OverlayView(
            id=overlay.id,
            api_id=overlay.api_id,
            vendor=vendor,
            name=name,
            version=version,
            status=overlay.status,
            document=overlay.document,
            target_revision_id=overlay.target_revision_id,
            confirmed_revision_id=overlay.confirmed_revision_id,
            contributed_by=overlay.contributed_by,
            confirmed_by_execution_id=overlay.confirmed_by_execution_id,
            created_at=overlay.created_at,
            updated_at=overlay.updated_at,
            confirmed_at=overlay.confirmed_at,
            deprecated_at=overlay.deprecated_at,
        )

    async def get(self, vendor: str, name: str, version: str, overlay_id: str) -> OverlayView:
        if not overlay_id.startswith("ovr_"):
            raise OverlayNotFoundError(overlay_id, vendor, name, version)

        api = await self._resolve_api(vendor, name, version)

        async with self._ctx.registry_db.session() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)

        if overlay is None:
            raise OverlayNotFoundError(overlay_id, vendor, name, version)

        return OverlayView(
            id=overlay.id,
            api_id=overlay.api_id,
            vendor=vendor,
            name=name,
            version=version,
            status=overlay.status,
            document=overlay.document,
            target_revision_id=overlay.target_revision_id,
            confirmed_revision_id=overlay.confirmed_revision_id,
            contributed_by=overlay.contributed_by,
            confirmed_by_execution_id=overlay.confirmed_by_execution_id,
            created_at=overlay.created_at,
            updated_at=overlay.updated_at,
            confirmed_at=overlay.confirmed_at,
            deprecated_at=overlay.deprecated_at,
        )

    async def list_page(
        self,
        vendor: str,
        name: str,
        version: str,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> OverlayPage:
        cursor_created_at: datetime | None = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor_str(cursor)

        api = await self._resolve_api(vendor, name, version)

        async with self._ctx.registry_db.session() as session:
            rows = await OverlayRepository.list_page(
                session,
                api_id=api.id,
                limit=limit + 1,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                status=status,
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        items = [
            OverlayPageItem(
                id=row.id,
                api_id=row.api_id,
                status=row.status,
                document=row.document,
                target_revision_id=row.target_revision_id,
                confirmed_revision_id=row.confirmed_revision_id,
                contributed_by=row.contributed_by,
                confirmed_by_execution_id=row.confirmed_by_execution_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
                confirmed_at=row.confirmed_at,
                deprecated_at=row.deprecated_at,
            )
            for row in rows
        ]

        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return OverlayPage(data=items, has_more=has_more, next_cursor=next_cursor)

    async def update(
        self,
        vendor: str,
        name: str,
        version: str,
        overlay_id: str,
        document: dict[str, Any] | None = None,
        target_revision_id: uuid.UUID | None = None,
        *,
        identity: Identity,
    ) -> OverlayView:
        if not overlay_id.startswith("ovr_"):
            raise OverlayNotFoundError(overlay_id, vendor, name, version)
        if document is not None:
            _validate_overlay_document(document)

        api = await self._resolve_api(vendor, name, version)

        # Decide the path in a short read. A materialized overlay (CONFIRMED with a non-null
        # confirmed_revision_id) is currently serving:
        #  - a *document* edit must re-materialize the new document onto the served spec (D1)
        #    — an operator action (overlays:confirm), handled by _rematerialize_on_edit;
        #  - a *metadata-only* edit (document is None, e.g. only target_revision_id) changes
        #    nothing served, so it stays an ordinary in-place field edit (apis:write) below.
        # A pending / stuck-unmaterialized overlay is always an in-place field edit below.
        async with self._ctx.registry_db.session() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if overlay is None:
                raise OverlayNotFoundError(overlay_id, vendor, name, version)
            materialized = (
                overlay.status == OverlayStatus.CONFIRMED
                and overlay.confirmed_revision_id is not None
            )

        if materialized and document is not None:
            return await self._rematerialize_on_edit(
                api,
                vendor,
                name,
                version,
                overlay_id,
                document=document,
                target_revision_id=target_revision_id,
                identity=identity,
            )

        async with self._ctx.registry_db.transaction() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if overlay is None:
                raise OverlayNotFoundError(overlay_id, vendor, name, version)

            is_materialized = (
                overlay.status == OverlayStatus.CONFIRMED
                and overlay.confirmed_revision_id is not None
            )
            if is_materialized:
                # A materialized overlay: a document edit is handled by the re-materialize
                # path above. Reaching here means either a metadata-only edit (allowed as an
                # in-place field edit — target_revision_id is advisory and changes nothing
                # served) or a concurrent confirm materialized the overlay after our read. A
                # document edit that raced into this branch must NOT be field-edited (it would
                # diverge the stored doc from the served revision) — send it back as a conflict
                # so the caller retries into the re-materialize path.
                if document is not None:
                    raise OverlayStateConflictError(
                        overlay_id, overlay.status, [OverlayStatus.PENDING], "update"
                    )
                await OverlayRepository.update_fields(
                    session,
                    overlay_id,
                    document=None,
                    target_revision_id=target_revision_id,
                )
                refreshed = await OverlayRepository.get_for_api(session, api.id, overlay_id)
                assert refreshed is not None
            else:
                # PENDING is the normal editable state. Also allow editing a stuck
                # CONFIRMED-but-unmaterialized overlay (confirmed_revision_id IS NULL): its
                # materialize job failed deterministically and re-confirm would only
                # re-enqueue the same failure, so editing the document is the operator's
                # escape hatch — it resets the overlay to PENDING for a fresh confirm.
                stuck_unmaterialized = overlay.status == OverlayStatus.CONFIRMED
                if overlay.status != OverlayStatus.PENDING and not stuck_unmaterialized:
                    raise OverlayStateConflictError(
                        overlay_id, overlay.status, [OverlayStatus.PENDING], "update"
                    )

                await OverlayRepository.update_fields(
                    session,
                    overlay_id,
                    document=document,
                    target_revision_id=target_revision_id,
                    reset_to_pending=stuck_unmaterialized,
                )

                refreshed = await OverlayRepository.get_for_api(session, api.id, overlay_id)
                assert refreshed is not None

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.OVERLAY,
            target_id=overlay_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            origin=identity.origin.value,
        )
        return self._view(vendor, name, version, refreshed)

    async def _rematerialize_on_edit(
        self,
        api: Api,
        vendor: str,
        name: str,
        version: str,
        overlay_id: str,
        *,
        document: dict[str, Any] | None,
        target_revision_id: uuid.UUID | None,
        identity: Identity,
    ) -> OverlayView:
        """Re-materialize a live, CONFIRMED overlay after an edit (D1).

        Editing a materialized overlay must rewrite the served spec, so this mirrors
        :meth:`confirm`'s materialize machinery instead of an in-place field edit:

        1. **Authorize.** Re-materializing rewrites what the platform serves — an operator
           action — so it requires ``overlays:confirm`` (not the ``apis:write`` that edits a
           pending overlay). Refuse with 403 otherwise, before mutating anything.
        2. **Validate preconditions + apply the edit over the clean base, BEFORE persisting.**
           A short read checks the overlay is still live (CONFIRMED and its
           ``confirmed_revision_id`` is the API's current revision) and carries a recorded
           pre-overlay base (``superseded_revision_id``); then the edited document is applied
           over *that* base (never the overlay's own current output, which would double-apply)
           and validated (openapi key, max_spec_bytes, SSRF on servers[].url). Validating
           before any write means a drifted/unsafe edit is rejected with the row unchanged.
        3. **Persist the edit + re-assert in a transaction** (document-re-read-in-tx guard):
           an edit/rollback/re-import could land during the apply window, so re-read the row
           and bail without enqueueing (idempotent) unless it is still the live confirmed
           overlay with the same clean base — the persisted document and the enqueued revision
           must agree.
        4. **Enqueue the re-ingest.** The worker ingests a new revision that supersedes the
           overlay's current revision (the full chain — clean base ← v1 ← v2 … — is retained,
           nothing is deleted) and re-links ``confirmed_revision_id`` to it, while
           ``CreateRevisionStage`` keeps the clean-base ``superseded_revision_id`` (it skips
           re-capturing when superseding an overlay revision) so a later rollback restores the
           clean upstream base, not an orphaned overlay output.
        """
        if not has_effective_permission(identity.permissions, "overlays:confirm"):
            raise OverlayRematerializeForbiddenError(overlay_id)

        # A re-materialize is only reached for a document edit (metadata-only edits of a
        # materialized overlay stay an in-place field edit in update()); document is non-None.
        assert document is not None

        # Read the overlay and validate the re-materialize preconditions in a short read,
        # then apply+validate the edited document over the pre-overlay clean base BEFORE
        # persisting anything — mirroring confirm, so a drifted/unsafe edit is rejected with
        # the overlay row unchanged (no doc/served divergence, no torn state). The base is
        # the overlay's superseded_revision_id (its original pre-overlay base, A5a) — never
        # the overlay's own current output, which would double-apply the patch.
        async with self._ctx.registry_db.session() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if overlay is None:
                raise OverlayNotFoundError(overlay_id, vendor, name, version)
            if overlay.status != OverlayStatus.CONFIRMED or overlay.confirmed_revision_id is None:
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "update"
                )
            live = await ApiRepository.get_by_id(session, api.id)
            if live is None or live.current_revision_id != overlay.confirmed_revision_id:
                # The overlay is not the currently-served revision (rolled back or
                # re-imported since) — there is no deterministic served spec to re-materialize
                # onto, so refuse rather than resurrect a stale overlay.
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "update"
                )
            base_revision_id = overlay.superseded_revision_id
            if base_revision_id is None:
                raise OverlayRollbackTargetMissingError(
                    overlay_id,
                    "no pre-overlay base revision was recorded (a first-ever materialize "
                    "superseded nothing, or the overlay predates superseded-revision "
                    "tracking) — cannot re-apply the edit over a clean base; roll back or "
                    "re-import upstream, then re-confirm",
                )
            # target_revision_id is advisory on the re-materialize path: the base is always
            # the recorded clean base (superseded_revision_id), never the caller's target.
            # Warn (don't fail) if the caller supplied a target that isn't that base, so the
            # divergence is observable — mirrors the confirm-time overlay_confirm_base_drift.
            if target_revision_id is not None and target_revision_id != base_revision_id:
                logger.warning(
                    "overlay_rematerialize_target_advisory",
                    overlay_id=overlay_id,
                    target_revision_id=str(target_revision_id),
                    base_revision_id=str(base_revision_id),
                    detail=(
                        "target_revision_id is advisory on re-materialize; the edit is "
                        "applied over the recorded pre-overlay base, not the target"
                    ),
                )

        base_content, base_source_url, base_digest = await self._load_base_spec_for_revision(
            api.id, overlay_id, base_revision_id
        )
        overlaid = self._apply_and_validate(overlay_id, base_content, document)

        # Persist the edit and re-assert the preconditions inside a transaction (an edit,
        # rollback, or re-import could have landed during the apply window). If the overlay
        # is no longer the live confirmed overlay, or its base moved, bail without enqueueing
        # — return the current view idempotently (like confirm's lost-race path). This is the
        # document-re-read-in-tx guard: the persisted document and the revision we enqueue
        # must agree, so we only enqueue when the row still matches what we validated.
        #
        # Ordering (persist then enqueue): if the enqueue fails after this commits, the row
        # holds the new document while confirmed_revision_id still points at the old revision
        # and the served spec is unchanged. That divergence self-heals — re-issuing the same
        # PATCH re-enters here and re-enqueues (a materialized-overlay document edit always
        # re-materializes; it never takes an idempotent no-op return), so there is a
        # deterministic recovery. We keep the row authoritative and let the served spec catch
        # up, consistent with confirm.
        async with self._ctx.registry_db.transaction() as session:
            current = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if (
                current is None
                or current.status != OverlayStatus.CONFIRMED
                or current.confirmed_revision_id is None
                or current.superseded_revision_id != base_revision_id
            ):
                refreshed = current or await self._get_overlay_or_raise(
                    api.id, overlay_id, vendor, name, version
                )
                return self._view(vendor, name, version, refreshed)
            live = await ApiRepository.get_by_id(session, api.id)
            if live is None or live.current_revision_id != current.confirmed_revision_id:
                return self._view(vendor, name, version, current)

            await OverlayRepository.update_fields(
                session,
                overlay_id,
                document=document,
                target_revision_id=target_revision_id,
            )

        # Enqueue the re-ingest. The worker supersedes the overlay's current revision with
        # the new one and re-links confirmed_revision_id (keeping the clean-base superseded
        # pointer — see CreateRevisionStage's overlay-over-overlay skip).
        await self._enqueue_materialize_job(
            vendor,
            name,
            version,
            overlay_id=overlay_id,
            overlaid_spec=overlaid,
            base_source_url=base_source_url,
            base_digest=base_digest,
            identity=identity,
        )

        refreshed = await self._get_overlay_or_raise(api.id, overlay_id, vendor, name, version)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.OVERLAY,
            target_id=overlay_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            reason="re-materialized on edit",
            origin=identity.origin.value,
        )
        return self._view(vendor, name, version, refreshed)

    async def confirm(
        self,
        vendor: str,
        name: str,
        version: str,
        overlay_id: str,
        execution_id: str | None = None,
        *,
        identity: Identity,
    ) -> OverlayView:
        if not overlay_id.startswith("ovr_"):
            raise OverlayNotFoundError(overlay_id, vendor, name, version)

        api = await self._resolve_api(vendor, name, version)

        # First transaction: read the overlay and decide what to do.
        # - PENDING → materialize (the normal path below).
        # - CONFIRMED with a materialized revision → idempotent no-op return.
        # - CONFIRMED but confirmed_revision_id is NULL → a prior confirm claimed the
        #   overlay but its materialize job never landed (enqueue failed / crash
        #   between the flip and the enqueue). Re-drive materialization idempotently.
        # - anything else (DEPRECATED) → conflict.
        async with self._ctx.registry_db.transaction() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if overlay is None:
                raise OverlayNotFoundError(overlay_id, vendor, name, version)

            if (
                overlay.status == OverlayStatus.CONFIRMED
                and overlay.confirmed_revision_id is not None
            ):
                return self._view(vendor, name, version, overlay)

            recovering = overlay.status == OverlayStatus.CONFIRMED
            if not recovering and overlay.status != OverlayStatus.PENDING:
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.PENDING], "confirm"
                )
            document = overlay.document
            target_revision_id = overlay.target_revision_id

        # Load the base spec + its provenance (source_url) and materialize BEFORE
        # flipping state, so a drifted/unsafe overlay is rejected while it is still
        # pending — no torn confirm, no orphaned ingest job.
        base_content, base_source_url, base_digest = await self._load_base_spec(
            vendor, name, version, overlay_id, target_revision_id
        )
        overlaid = self._apply_and_validate(overlay_id, base_content, document)

        # Atomically claim the overlay: flip PENDING→CONFIRMED with a compare-and-swap
        # so that at most one concurrent confirm proceeds to enqueue a materialize job.
        # In the recovery path the overlay is already CONFIRMED (with a null revision),
        # so we skip the claim and just re-enqueue.
        #
        # We also re-read the document inside the claim transaction and bail if it
        # changed since tx1: apply+validate above runs outside any transaction and can
        # take a while on a multi-MB spec, and an ``update`` landing in that window is
        # legal (the overlay is still PENDING). Without this guard the CAS would still
        # succeed and we'd enqueue a job carrying the *old* document while the row stores
        # the *new* one — confirmed_revision_id would then point at a revision that does
        # not embody the stored document (the same doc/served divergence this PR defers
        # for update-after-confirm, but inside a single confirm). Treat a changed
        # document as a lost race and return the current (still PENDING) view so the
        # caller re-confirms against the new document.
        if not recovering:
            async with self._ctx.registry_db.transaction() as session:
                current = await OverlayRepository.get_for_api(session, api.id, overlay_id)
                if (
                    current is None
                    or current.status != OverlayStatus.PENDING
                    or current.document != document
                ):
                    refreshed = current or await self._get_overlay_or_raise(
                        api.id, overlay_id, vendor, name, version
                    )
                    return self._view(vendor, name, version, refreshed)
                now = datetime.now(UTC)
                claimed = await OverlayRepository.set_status(
                    session,
                    overlay_id,
                    OverlayStatus.CONFIRMED,
                    confirmed_at=now,
                    confirmed_by_execution_id=execution_id,
                    expected_status=OverlayStatus.PENDING,
                )
            if claimed == 0:
                # Lost the race to a concurrent confirm/transition — return the current
                # state idempotently rather than enqueueing a duplicate materialize job.
                refreshed = await self._get_overlay_or_raise(
                    api.id, overlay_id, vendor, name, version
                )
                return self._view(vendor, name, version, refreshed)

        # Enqueue the re-ingest that actually rewrites the served spec. This lives in
        # admin_db (the job queue), separate from the registry-side status flip above.
        # If this fails after the flip, the overlay is CONFIRMED with a null
        # confirmed_revision_id; a later re-confirm takes the recovery path and
        # re-enqueues. The worker sets confirmed_revision_id on success.
        await self._enqueue_materialize_job(
            vendor,
            name,
            version,
            overlay_id=overlay_id,
            overlaid_spec=overlaid,
            base_source_url=base_source_url,
            base_digest=base_digest,
            identity=identity,
        )

        refreshed = await self._get_overlay_or_raise(api.id, overlay_id, vendor, name, version)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CONFIRM,
            target_type=AuditTargetType.OVERLAY,
            target_id=overlay_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            reason=f"confirmed by execution {execution_id}" if execution_id else None,
            origin=identity.origin.value,
        )
        return self._view(vendor, name, version, refreshed)

    async def deprecate(
        self, vendor: str, name: str, version: str, overlay_id: str, *, identity: Identity
    ) -> None:
        if not overlay_id.startswith("ovr_"):
            raise OverlayNotFoundError(overlay_id, vendor, name, version)

        api = await self._resolve_api(vendor, name, version)

        async with self._ctx.registry_db.transaction() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if overlay is None:
                raise OverlayNotFoundError(overlay_id, vendor, name, version)

            now = datetime.now(UTC)
            await OverlayRepository.set_status(
                session, overlay_id, OverlayStatus.DEPRECATED, deprecated_at=now
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DEPRECATE,
            target_type=AuditTargetType.OVERLAY,
            target_id=overlay_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            origin=identity.origin.value,
        )

    async def rollback(
        self, vendor: str, name: str, version: str, overlay_id: str, *, identity: Identity
    ) -> None:
        """Un-confirm a materialized overlay: restore the revision it superseded (A5b).

        Reverses a confirm. The overlay must be CONFIRMED and *currently live* (its
        ``confirmed_revision_id`` is the API's current revision), and must carry a
        ``superseded_revision_id`` (recorded at materialize time, A5a) that is still an
        archived revision. In one registry transaction, guarded by compare-and-swaps so a
        concurrent confirm/re-import can't double-flip:

        1. archive the current overlay revision (CAS on it being active),
        2. restore the superseded revision (ARCHIVED → IMPORTED),
        3. flip ``current_revision_id`` overlay → superseded (CAS on the expected prior),
        4. mark the overlay DEPRECATED (CAS on CONFIRMED).

        Ordering archive-before-restore keeps the one-active partial unique index
        satisfied throughout (never two active revisions). Any CAS returning 0 means
        another transition raced us; we raise a state conflict and change nothing.
        """
        if not overlay_id.startswith("ovr_"):
            raise OverlayNotFoundError(overlay_id, vendor, name, version)

        api = await self._resolve_api(vendor, name, version)

        async with self._ctx.registry_db.transaction() as session:
            overlay = await OverlayRepository.get_for_api(session, api.id, overlay_id)
            if overlay is None:
                raise OverlayNotFoundError(overlay_id, vendor, name, version)
            if overlay.status != OverlayStatus.CONFIRMED:
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "rollback"
                )

            live = await ApiRepository.get_by_id(session, api.id)
            overlay_revision_id = overlay.confirmed_revision_id
            if (
                overlay_revision_id is None
                or live is None
                or live.current_revision_id != overlay_revision_id
            ):
                # The overlay isn't the currently-served revision (already rolled back,
                # re-imported, or never fully materialized) — nothing deterministic to
                # reverse here.
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "rollback"
                )

            superseded_id = overlay.superseded_revision_id
            if superseded_id is None:
                raise OverlayRollbackTargetMissingError(
                    overlay_id,
                    "no superseded revision was recorded (a first-ever materialize "
                    "superseded nothing, or the overlay predates superseded-revision "
                    "tracking) — resolve manually, e.g. by re-importing upstream",
                )

            # 1. Archive the current overlay revision (CAS on it being active).
            if await ApiRevisionRepository.archive_one(session, overlay_revision_id) == 0:
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "rollback"
                )
            # 2. Restore the superseded revision (must still be archived).
            restored = await ApiRevisionRepository.restore_archived_to_imported(
                session, superseded_id
            )
            if restored == 0:
                raise OverlayRollbackTargetMissingError(
                    overlay_id,
                    f"superseded revision '{superseded_id}' is no longer restorable "
                    "(deleted or not archived) — resolve manually",
                )
            # 3. Flip current overlay → superseded (CAS on the expected prior pointer).
            flipped = await ApiRepository.compare_and_set_current_revision(
                session,
                api.id,
                expected_revision_id=overlay_revision_id,
                new_revision_id=superseded_id,
            )
            if flipped == 0:
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "rollback"
                )
            # 4. Mark the overlay DEPRECATED (CAS on it still being CONFIRMED).
            now = datetime.now(UTC)
            demoted = await OverlayRepository.set_status(
                session,
                overlay_id,
                OverlayStatus.DEPRECATED,
                deprecated_at=now,
                expected_status=OverlayStatus.CONFIRMED,
            )
            if demoted == 0:
                raise OverlayStateConflictError(
                    overlay_id, overlay.status, [OverlayStatus.CONFIRMED], "rollback"
                )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DEPRECATE,
            target_type=AuditTargetType.OVERLAY,
            target_id=overlay_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=str(api.id),
            reason=f"rolled back; restored revision {overlay.superseded_revision_id}",
            origin=identity.origin.value,
        )
