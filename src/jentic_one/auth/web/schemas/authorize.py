"""Web schemas for the /authorize approval-in-flow endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jentic_one.shared.web.sensitive import SENSITIVE


class OAuthApprovalStatusResponse(BaseModel):
    """Minimal tri-state approval status for a pending-client authorize request.

    Deliberately carries nothing else — no client name, redirect URIs, or
    metadata — so the anonymous poll endpoint cannot be used to read client
    details out of the registry.
    """

    status: Literal["pending", "approved", "denied"]


class OAuthApprovalDecisionRequest(BaseModel):
    """Inline admin approve/deny posted from the approval-pending page.

    ``state`` is the signed approval-state blob minted by ``/authorize`` for
    this exact authorize request — the decision endpoint never accepts a bare
    ``client_id``.
    """

    state: str = Field(json_schema_extra=SENSITIVE)
    action: Literal["approve", "deny"]
