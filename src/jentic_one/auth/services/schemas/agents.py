"""Agent service-layer view schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from jentic_one.shared.auth.grant_policy import (
    GrantEndReason,
    GrantPolicy,
    grant_end_reason,
    idle_deadline,
)
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
    permissions: list[str] | None = None


class ApiLastUseView(BaseModel):
    """When an agent last called one API through a bound credential."""

    api_vendor: str
    # None when the recorded calls carried no API name.
    api_name: str | None = None
    api_version: str | None = None
    last_used_at: datetime


class CredentialBindingView(BaseModel):
    """Read-model for a direct agent↔credential binding (theme 5 phase 1)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    credential_id: str
    # Human-readable credential name resolved from the control DB. None when
    # the credential no longer exists or the control DB is unreachable.
    name: str | None = None
    # Credential lifecycle from the control DB; None when it cannot be resolved.
    state: str | None = None
    bound_at: datetime
    # Reversible per-consumer cut-off: excluded from derivation, rules kept.
    suspended: bool = False
    # Why the binding is suspended: None for a manual suspension,
    # ``api_deleted`` when the API its credential serves was deleted.
    suspended_reason: str | None = None
    # When the binding stops authorizing calls; None never expires. Independent
    # of ``suspended``.
    expires_at: datetime | None = None
    # Idle limit in seconds (None = none), and when the binding was granted or
    # last renewed — the idle clock restarts at the later of that and the
    # column ``last_used_at`` (``idle_clock_used_at`` below).
    max_idle_period: int | None = None
    renewed_at: datetime | None = None
    # Opt-in warning to the owner before the binding ends (idle limit or end date).
    idle_warning: bool = False
    # What a call through the binding gets once it has ended (``AfterExpiry``).
    after_expiry: str = "hold"
    # Use count (None = unlimited) and the uses left; a renewal restores it.
    max_quota: int | None = None
    quota_remaining: int | None = None
    # The binding's own ``last_used_at`` column, read from the row: the idle
    # clock's use stamp. ``last_used_at`` below is the display value.
    idle_clock_used_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("idle_clock_used_at", "last_used_at")
    )
    # Shared permission rule set the binding points at (control DB, Q-04).
    # None means the binding's inline rules apply.
    rule_set_id: str | None = None
    # The API the bound credential serves (control DB) — the credential-side
    # analogue of the toolkit `serves` list (issue #686).
    serves: list[ServedApiRef] = []
    # Latest recorded execution by this agent through this credential (admin
    # ``execution_records``); None when it has never been used.
    last_used_at: datetime | None = None
    # The same, per API the agent called through the credential (most recent
    # first). A vendor-wide binding fans out to one entry per API used.
    last_used_by_api: list[ApiLastUseView] = []

    @property
    def policy(self) -> GrantPolicy:
        """The binding's policy against the idle clock's own use stamp."""
        return GrantPolicy(
            expires_at=self.expires_at,
            max_idle_period=self.max_idle_period,
            last_used_at=self.idle_clock_used_at,
            renewed_at=self.renewed_at,
            bound_at=self.bound_at,
            max_quota=self.max_quota,
            quota_remaining=self.quota_remaining,
        )

    @property
    def idle_expires_at(self) -> datetime | None:
        """When the idle limit ends the binding if it goes unused; None without one."""
        return idle_deadline(self.policy)

    @property
    def expired_reason(self) -> GrantEndReason | None:
        """Which limit ended the binding, or ``None`` while it holds."""
        return grant_end_reason(self.policy)

    @property
    def expired(self) -> bool:
        """Whether any limit of the binding has been reached."""
        return self.expired_reason is not None


class EndingSoonBindingView(CredentialBindingView):
    """A binding with the warning on whose idle limit or end date is close."""

    agent_name: str
    # The nearer deadline due a warning, and which limit it is.
    ends_at: datetime
    ending: str
