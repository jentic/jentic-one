"""Web schemas for the /authorize approval-in-flow endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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

    ``state`` is deliberately NOT marked x-sensitive: the CLI's GEN-21
    redaction backstop unions every sensitive field's BARE name globally, and
    "state" is generic enough to redact unrelated CLI output (e.g. the MCP
    session-diagnosis ``state`` field). The blob is not a lasting bearer
    credential — it is HMAC-signed, purpose-discriminated, TTL'd (600 s), and
    the decision endpoint additionally requires an authenticated admin with
    ``oauth-clients:write`` — so global redaction buys nothing worth that
    collision.
    """

    state: str
    action: Literal["approve", "deny"]
