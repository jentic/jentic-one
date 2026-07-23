"""API service — listing, retrieval, and mutation of API aggregates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel

from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.control_credential_boundary_repo import (
    ControlCredentialBoundaryRepository,
)
from jentic_one.registry.services.errors import ApiNotFoundError, NoCurrentRevisionError
from jentic_one.registry.web.schemas.apis import (
    SecuritySchemeFlowResponse,
    SecuritySchemeListResponse,
    SecuritySchemeResponse,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
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
            if revision_ids:
                security_types = await ApiRepository.load_security_scheme_types(
                    session, revision_ids
                )
                server_hosts = await ApiRepository.load_server_hosts(session, revision_ids)

            for row in rows:
                rev = row.current_revision_id
                host = server_hosts.get(rev) if rev else None
                schemes = security_types.get(rev, []) if rev else []
                items.append(
                    ApiPageItem(
                        id=row.id,
                        vendor=row.vendor,
                        name=row.name,
                        version=row.version,
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
        async with self._ctx.registry_db.transaction() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)
            await ApiRepository.delete(session, api.id)

        deactivated = await self._deactivate_control_credentials(vendor, name, version)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.API,
            target_id=str(api.id),
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            before={"vendor": vendor, "name": name, "version": version},
            after={"deactivated_credentials": deactivated},
            origin=identity.origin.value,
        )

    async def _deactivate_control_credentials(self, vendor: str, name: str, version: str) -> int:
        """Deactivate control credentials stranded by this API delete.

        Cross-DB and best-effort: the registry delete has already committed, and
        the two databases cannot share a transaction (no 2PC). Deactivating (not
        deleting) removes the credential from the broker resolver's active-match
        set so a re-import can't collide with it (issue #643), while preserving
        the row for the operator to see/rotate. When the deployment topology
        denies this process control-DB access (registry-only parts mode), there
        is nothing to reconcile here — skip quietly.
        """
        if not self._ctx.is_db_allowed("control"):
            return 0
        try:
            async with self._ctx.control_db.transaction() as session:
                return await ControlCredentialBoundaryRepository.deactivate_credentials_for_api(
                    session, api_vendor=vendor, api_name=name, api_version=version
                )
        except Exception:
            logger.warning(
                "control_credential_deactivation_failed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
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

        if api.current_revision is not None:
            revision = api.current_revision
            if revision.servers:
                parsed = urlparse(revision.servers[0].url)
                host = parsed.hostname
            if revision.security_schemes:
                security_schemes = sorted({s.type for s in revision.security_schemes})

        return ApiView(
            vendor=api.vendor,
            name=api.name,
            version=api.version,
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
        )
