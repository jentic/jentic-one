"""Pydantic view/result models for the access requests service."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict


class CollectedResourceIds(BaseModel):
    """Unique toolkit and credential IDs extracted from access request items."""

    toolkit_ids: list[str]
    credential_ids: list[str]


class ResolvedNames(BaseModel):
    """Batch-resolved toolkit and credential display names keyed by ID."""

    toolkit_names: dict[str, str] = {}
    credential_names: dict[str, str] = {}


class AccessRequestItemView(BaseModel):
    """View model for a single access-request line item."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    resource_type: str
    action: str
    resource_id: str | None
    resource_reference: dict[str, Any] | None
    to_type: str | None
    to_id: str | None
    toolkit_name: str | None = None
    credential_name: str | None = None
    rules: list[dict[str, Any]] | None
    status: str
    applied_effects: dict[str, Any] | None
    decided_by: str | None
    decided_at: dt.datetime | None
    decision_reason: str | None


class EvaluationCheck(BaseModel):
    """A single evaluation check result."""

    check: str
    passed: bool
    blocker: str | None = None


class Evaluation(BaseModel):
    """Computed evaluation of whether the caller can fulfill a request."""

    can_fulfill: bool
    checks: list[EvaluationCheck]


class FilerOwnerView(BaseModel):
    """Display info for the filer's human owner, resolved cross-DB (labelling only)."""

    id: str
    email: str
    display_name: str | None = None


class AccessRequestView(BaseModel):
    """View model for an access request envelope."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str
    reason: str | None
    requested_by: str
    status: str
    approve_url: str
    filed_at: dt.datetime
    expires_at: dt.datetime
    created_by: str
    filer_owner_id: str | None
    filer_owner: FilerOwnerView | None = None
    items: list[AccessRequestItemView]
    evaluation: Evaluation | None = None


class AccessRequestPage(BaseModel):
    """Paginated list of access request views."""

    data: list[AccessRequestView]
    has_more: bool
    next_cursor: str | None = None
