"""API service — listing, retrieval, and mutation of API aggregates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel

from jentic_one.registry.repos.admin_credential_binding_boundary_repo import (
    SUSPENDED_REASON_API_DELETED,
    AdminCredentialBindingBoundaryRepository,
)
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.repos.control_credential_boundary_repo import (
    ControlCredentialBoundaryRepository,
)
from jentic_one.registry.services.errors import ApiNotFoundError, NoCurrentRevisionError
from jentic_one.registry.web.schemas.apis import (
    SecuritySchemeFlowResponse,
    SecuritySchemeListResponse,
    SecuritySchemeResponse,
)
from jentic_one.shared.audit import (
    AuditAction,
    AuditTargetType,
    record_audit,
    record_audit_best_effort,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor, encode_cursor

logger = structlog.get_logger()


class ApiPageItem(BaseModel):
    """View model for a single API in a paginated list.

    ``GET /apis`` lists APIs imported into this deployment — the local registry.
    The public catalog of importable-but-not-yet-imported APIs is a separate
    surface (``GET /catalog``).
    """

    id: uuid.UUID
    vendor: str
    name: str
    version: str
    catalog_api_id: str | None
    display_name: str | None
    description: str | None
    icon_url: str | None
    current_revision_id: uuid.UUID | None
    revision_count: int
    operation_count: int
    security_schemes: list[str]
    host: str | None
    created_at: datetime
    updated_at: datetime
    origin: str | None = None
    source_url: str | None = None
    update_available: bool = False


class ApiPage(BaseModel):
    """Paginated result of APIs."""

    data: list[ApiPageItem]
    has_more: bool
    next_cursor: str | None = None


@dataclass(frozen=True)
class ApiView:
    """Resolved view of an Api aggregate with derived fields."""

    vendor: str
    name: str
    version: str
    catalog_api_id: str | None
    display_name: str | None
    description: str | None
    icon_url: str | None
    current_revision_id: str | None
    revision_count: int
    operation_count: int
    host: str | None
    security_schemes: list[str]
    created_at: datetime
    updated_at: datetime
    #: Provenance of the current revision (``"catalog"``/``"overlay"``/``None`` manual)
    #: and its upstream spec URL — the #648 provenance backend half. ``update_available``
    #: is true when this API is catalog-tracked and has an un-adopted upstream update.
    origin: str | None = None
    source_url: str | None = None
    update_available: bool = False


@dataclass(frozen=True)
class _ControlCleanup:
    """What the control-DB step of an API delete retired."""

    credential_ids: list[str] = field(default_factory=list)
    deactivated: int = 0


class ApiService:
    """Read and write operations for the Api aggregate."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        *,
        vendor: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ApiPage:
        """List locally registered APIs (cursor-paginated).

        ``GET /apis`` is the **imported** registry — APIs that exist in this
        deployment. The public catalog (APIs available to import but not yet
        imported) is a separate surface at ``GET /catalog``; the two are no longer
        blended into one list (that conflated "what you have" with "what you could
        have" and broke pagination). The Discover UI composes the two surfaces.
        """
        cursor_created_at = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor(cursor)

        items: list[ApiPageItem] = []
        next_cursor: str | None = None

        async with self._ctx.registry_db.session() as session:
            rows = await ApiRepository.list_page(
                session,
                limit=limit + 1,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                vendor=vendor,
            )

            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]

            revision_ids = [
                r.current_revision_id for r in rows if r.current_revision_id is not None
            ]
            security_types: dict[uuid.UUID, list[str]] = {}
            server_hosts: dict[uuid.UUID, str | None] = {}
            provenance: dict[uuid.UUID, tuple[str | None, str | None]] = {}
            outdated_api_ids: set[uuid.UUID] = set()
            if revision_ids:
                security_types = await ApiRepository.load_security_scheme_types(
                    session, revision_ids
                )
                server_hosts = await ApiRepository.load_server_hosts(session, revision_ids)
                provenance = await ApiRepository.load_revision_provenance(session, revision_ids)
                outdated_api_ids = await CatalogUpdateCheckRepository.outdated_api_ids(session)

            for row in rows:
                rev = row.current_revision_id
                host = server_hosts.get(rev) if rev else None
                schemes = security_types.get(rev, []) if rev else []
                origin, source_url = provenance.get(rev, (None, None)) if rev else (None, None)
                items.append(
                    ApiPageItem(
                        id=row.id,
                        vendor=row.vendor,
                        name=row.name,
                        version=row.version,
                        catalog_api_id=row.catalog_api_id,
                        display_name=row.display_name,
                        description=row.description,
                        icon_url=row.icon_url,
                        current_revision_id=row.current_revision_id,
                        revision_count=row.revision_count,
                        operation_count=row.operation_count,
                        security_schemes=schemes,
                        host=host,
                        created_at=row.created_at,
                        updated_at=row.updated_at or row.created_at,
                        origin=origin,
                        source_url=source_url,
                        update_available=row.id in outdated_api_ids,
                    )
                )

            if has_more and rows:
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, str(last.id))

        return ApiPage(data=items, has_more=has_more, next_cursor=next_cursor)

    async def get_by_identity(self, vendor: str, name: str, version: str) -> ApiView:
        """Retrieve a single API by its (vendor, name, version) identity."""
        async with self._ctx.registry_db.session() as session:
            return await self._fetch_api_view(session, vendor, name, version)

    async def update(
        self, vendor: str, name: str, version: str, *, fields: dict[str, Any], identity: Identity
    ) -> ApiView:
        async with self._ctx.registry_db.transaction() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)
            await ApiRepository.update_presentation(session, api.id, fields=fields)
            view = await self._fetch_api_view(session, vendor, name, version)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.API,
            target_id=str(api.id),
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            after={"fields": sorted(fields.keys())},
            origin=identity.origin.value,
        )
        return view

    async def delete(self, vendor: str, name: str, version: str, *, identity: Identity) -> None:
        """Delete an API and retire the credential access tied to it.

        After the registry delete commits, the control credentials stored for
        the exact API identity are deactivated (#643) and every agent binding
        to them is suspended with reason ``api_deleted`` (#1168). Re-importing
        a spec under the same ``(vendor, name, version)`` therefore never
        silently re-adopts the old bindings and permission rules: an owner
        restores each one deliberately with the binding ``:resume`` action
        (and re-activates the credential). Both cross-database steps are
        best-effort; the registry and control/admin databases cannot share
        a transaction. Vendor-wide credentials (``NULL`` name/version) are
        left alone because they also serve the vendor's other APIs.
        """
        async with self._ctx.registry_db.transaction() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)
            await ApiRepository.delete(session, api.id)

        control = await self._retire_control_credentials(vendor, name, version)
        suspended = await self._suspend_agent_bindings(
            control.credential_ids, api_id=str(api.id), identity=identity
        )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.API,
            target_id=str(api.id),
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            before={"vendor": vendor, "name": name, "version": version},
            after={
                "deactivated_credentials": control.deactivated,
                # Deprecated: always empty since theme-5 Phase 6b dropped the
                # toolkit tables. Kept so the audit ``after`` shape is stable
                # for readers of historical and new rows alike.
                "removed_toolkit_bindings": [],
                "suspended_bindings": suspended,
            },
            origin=identity.origin.value,
        )

    async def _retire_control_credentials(
        self, vendor: str, name: str, version: str
    ) -> _ControlCleanup:
        """Deactivate control credentials stranded by this API delete.

        Collects the ids of every credential stored for the exact API identity
        (active or not, so their agent bindings can be suspended), deactivates
        the active ones, in one control transaction.

        Cross-DB and best-effort: the registry delete has already committed, and
        the two databases cannot share a transaction (no 2PC). Deactivating (not
        deleting) removes the credential from the broker resolver's active-match
        set so a re-import can't collide with it (issue #643), while preserving
        the row for the operator to see/rotate. Every deployed topology that
        serves the registry has control-DB access (``SURFACE_DB_DEPS``), so a
        missing grant only happens in a narrowed ad-hoc context; it is logged
        rather than failing the (already committed) delete.
        """
        if not self._ctx.is_db_allowed("control"):
            logger.warning(
                "api_delete_credential_cleanup_skipped",
                reason="control_db_not_allowed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
            )
            return _ControlCleanup()
        try:
            async with self._ctx.control_db.transaction() as session:
                credential_ids = await ControlCredentialBoundaryRepository.credential_ids_for_api(
                    session, api_vendor=vendor, api_name=name, api_version=version
                )
                deactivated = (
                    await ControlCredentialBoundaryRepository.deactivate_credentials_for_api(
                        session, api_vendor=vendor, api_name=name, api_version=version
                    )
                )
                return _ControlCleanup(credential_ids=credential_ids, deactivated=deactivated)
        except Exception:
            logger.warning(
                "control_credential_deactivation_failed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
                exc_info=True,
            )
            return _ControlCleanup()

    async def _suspend_agent_bindings(
        self, credential_ids: list[str], *, api_id: str, identity: Identity
    ) -> int:
        """Suspend agent bindings to the deleted API's credentials; return the count.

        Each suspended binding gets an audit entry (``DISABLE`` on the
        binding, reason ``api_deleted``) and a ``credential.unbound_from_agent``
        event, written in the same admin transaction as the suspension.
        Permission rules are kept, so ``:resume`` restores the binding exactly
        as it was. Best-effort like the credential deactivation: skipped when
        this process has no admin-DB access, logged on failure.
        """
        if not credential_ids:
            return 0
        if not self._ctx.is_db_allowed("admin"):
            logger.warning(
                "agent_binding_suspension_skipped",
                reason="admin_db_not_allowed",
                api_id=api_id,
                credential_count=len(credential_ids),
            )
            return 0
        try:
            async with self._ctx.admin_db.transaction() as session:
                suspended = (
                    await AdminCredentialBindingBoundaryRepository.suspend_bindings_for_credentials(
                        session, credential_ids=credential_ids, reason=SUSPENDED_REASON_API_DELETED
                    )
                )
                for binding in suspended:
                    await record_audit(
                        session,
                        action=AuditAction.DISABLE,
                        target_type=AuditTargetType.CREDENTIAL_BINDING,
                        target_id=binding.credential_id,
                        actor_type=identity.actor_type,
                        actor_id=identity.sub,
                        target_parent_id=binding.agent_id,
                        reason=SUSPENDED_REASON_API_DELETED,
                        after={"suspended": True, "api_id": api_id},
                        origin=identity.origin.value,
                    )
                    await emit_event_best_effort(
                        session,
                        type=EventType.CREDENTIAL_UNBOUND_FROM_AGENT,
                        severity=EventSeverity.INFO,
                        summary=(
                            f"Credential {binding.credential_id} suspended for agent "
                            f"{binding.agent_id} because its API was deleted"
                        ),
                        created_by=identity.sub,
                        actor_id=identity.sub,
                        actor_type=identity.actor_type.value,
                    )
                return len(suspended)
        except Exception:
            logger.warning(
                "agent_binding_suspension_failed",
                api_id=api_id,
                credential_count=len(credential_ids),
                exc_info=True,
            )
            return 0

    async def get_security_schemes(
        self, vendor: str, name: str, version: str
    ) -> SecuritySchemeListResponse:
        """Return typed security scheme details for an API's current revision."""
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier_with_current_revision(
                session, vendor, name, version
            )
            if api is None:
                raise ApiNotFoundError(vendor, name, version)
            if api.current_revision is None:
                raise NoCurrentRevisionError(vendor, name, version)

            data: list[SecuritySchemeResponse] = []
            for scheme in api.current_revision.security_schemes:
                flows = [
                    SecuritySchemeFlowResponse(
                        flow_type=flow.flow_type,
                        authorization_url=flow.authorization_url,
                        token_url=flow.token_url,
                        refresh_url=flow.refresh_url,
                        scopes=flow.scopes,
                    )
                    for flow in scheme.flows
                ]
                data.append(
                    SecuritySchemeResponse(
                        name=scheme.name,
                        type=scheme.type,
                        scheme=scheme.scheme,
                        bearer_format=scheme.bearer_format,
                        in_location=scheme.in_location,
                        param_name=scheme.param_name,
                        open_id_connect_url=scheme.open_id_connect_url,
                        description=scheme.description,
                        flows=flows,
                    )
                )

            return SecuritySchemeListResponse(data=data)

    @staticmethod
    async def _fetch_api_view(session: Any, vendor: str, name: str, version: str) -> ApiView:
        api = await ApiRepository.get_by_identifier_with_current_revision(
            session, vendor, name, version
        )
        if api is None:
            raise ApiNotFoundError(vendor, name, version)

        host: str | None = None
        security_schemes: list[str] = []
        origin: str | None = None
        source_url: str | None = None

        if api.current_revision is not None:
            revision = api.current_revision
            origin = revision.origin
            source_url = revision.source_url
            if revision.servers:
                parsed = urlparse(revision.servers[0].url)
                host = parsed.hostname
            if revision.security_schemes:
                security_schemes = sorted({s.type for s in revision.security_schemes})

        # Keyed on api_id (not source_url): outdated_api_ids only ever contains APIs
        # that have a check row *and* whose served revision differs from the notified
        # upstream digest, so membership is exact. We deliberately do NOT gate on the
        # current revision's source_url: when a manually PUBLISHED revision (source_url
        # None) supersedes a catalog import, the API stays legitimately outdated (the
        # documented published-over-catalog caveat), and the list surface flags it — so
        # the detail view must agree or the badge and the ribbon contradict each other
        # for exactly that case. The extra query on a single-API read is negligible.
        outdated = await CatalogUpdateCheckRepository.outdated_api_ids(session)
        update_available = api.id in outdated

        return ApiView(
            vendor=api.vendor,
            name=api.name,
            version=api.version,
            catalog_api_id=api.catalog_api_id,
            display_name=api.display_name,
            description=api.description,
            icon_url=api.icon_url,
            current_revision_id=str(api.current_revision_id) if api.current_revision_id else None,
            revision_count=api.revision_count,
            operation_count=api.operation_count,
            host=host,
            security_schemes=security_schemes,
            created_at=api.created_at,
            updated_at=api.updated_at or api.created_at,
            origin=origin,
            source_url=source_url,
            update_available=update_available,
        )
