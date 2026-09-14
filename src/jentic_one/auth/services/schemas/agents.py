"""Agent service-layer view schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jentic_one.shared.schemas import ServedApiRef


class AgentView(BaseModel):
    """Read-model for an agent record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    registered_by: str
    parent_agent_id: str | None = None
    approved_by: str | None = None
    status: str
    denial_reason: str | None = None
    denied_by: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    has_api_key: bool = False


class AgentCreatePayload(BaseModel):
    """Payload for creating an agent manually."""

    name: str
    description: str | None = None
    scopes: list[str] | None = None


class ToolkitBindingView(BaseModel):
    """Read-model for a toolkit binding."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    toolkit_id: str
    # Human-readable toolkit name resolved from the control DB (issue #686).
    # None when the toolkit no longer exists or the control DB is unreachable.
    name: str | None = None
    bound_at: datetime
    # APIs the bound toolkit serves, derived from its credentials (control DB).
    serves: list[ServedApiRef] = []


class CredentialBindingView(BaseModel):
    """Read-model for a direct agent↔credential binding (theme 5 phase 1)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    credential_id: str
    # Human-readable credential name resolved from the control DB. None when
    # the credential no longer exists or the control DB is unreachable.
    name: str | None = None
    bound_at: datetime
    # Reversible per-consumer cut-off: excluded from derivation, rules kept.
    suspended: bool = False
    # Shared permission rule set the binding points at (control DB, Q-04).
    # None means the binding's inline rules apply.
    rule_set_id: str | None = None
    # The API the bound credential serves (control DB) — the credential-side
    # analogue of the toolkit `serves` list (issue #686).
    serves: list[ServedApiRef] = []
