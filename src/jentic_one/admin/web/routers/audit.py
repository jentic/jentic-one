"""Audit log router — read-only query endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from jentic_one.admin.services.audit_service import AuditService
from jentic_one.admin.services.schemas.audit import AuditFilter, AuditView
from jentic_one.admin.web.deps import get_audit_service
from jentic_one.admin.web.schemas.audit import AuditListResponse, AuditResponse
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.audit import AuditTargetType
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex

router = APIRouter()


def _audit_response(view: AuditView) -> AuditResponse:
    """Project an AuditView to an AuditResponse."""
    return AuditResponse(
        id=view.id,
        occurred_at=view.occurred_at,
        action=view.action,
        target_type=view.target_type,
        target_id=view.target_id,
        target_parent_id=view.target_parent_id,
        actor_type=view.actor_type,
        actor_id=view.actor_id,
        actor_session_id=view.actor_session_id,
        before=view.before,
        after=view.after,
        diff=view.diff,
        request_id=view.request_id,
        trace_id=view.trace_id,
        job_id=view.job_id,
        reason=view.reason,
        ip_address=view.ip_address,
        user_agent=view.user_agent,
        origin=view.origin,
    )


@router.get("/audit")
async def list_audit_entries(
    identity: Identity = get_current_identity(required_permissions=["audit:read"]),
    audit_svc: AuditService = Depends(get_audit_service),
    target_type: AuditTargetType | None = Query(default=None, openapi_examples=ex.example("agent")),
    target_id: str | None = Query(default=None, openapi_examples=ex.example(ex.AGENT_ID)),
    actor_id: str | None = Query(default=None, openapi_examples=ex.example(ex.AGENT_ID)),
    origin: str | None = Query(default=None, openapi_examples=ex.example("dashboard")),
    since: datetime | None = Query(default=None, openapi_examples=ex.example(ex.DATETIME_FROM)),
    until: datetime | None = Query(default=None, openapi_examples=ex.example(ex.DATETIME_TO)),
    cursor: str | None = Query(default=None, openapi_examples=ex.example(ex.CURSOR)),
    limit: int = Query(default=50, ge=1, le=200, openapi_examples=ex.example(25)),
) -> AuditListResponse:
    """List audit entries with optional filters.

    Scoped to the caller: their own entries plus those made by agents they own.
    Org admins see every entry.
    """
    page = await audit_svc.list_all(
        filter=AuditFilter(
            target_type=target_type,
            target_id=target_id,
            actor_id=actor_id,
            origin=origin,
            since=since,
            until=until,
        ),
        identity=identity,
        cursor=cursor,
        limit=limit,
    )
    return AuditListResponse(
        data=[_audit_response(e) for e in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/audit/{audit_id}")
async def get_audit_entry(
    audit_id: Annotated[str, Path(openapi_examples=ex.example(ex.AUDIT_ID))],
    identity: Identity = get_current_identity(required_permissions=["audit:read"]),
    audit_svc: AuditService = Depends(get_audit_service),
) -> AuditResponse:
    """Get a single audit entry by ID."""
    view = await audit_svc.get_by_id(audit_id, identity=identity)
    return _audit_response(view)
