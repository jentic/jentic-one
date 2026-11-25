"""Agent request/response schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jentic_one.shared.auth.grant_policy import (
    AFTER_EXPIRY_DESCRIPTION,
    MAX_QUOTA,
    WARNING_DESCRIPTION,
    AfterExpiry,
    GrantEndReason,
)
from jentic_one.shared.iso_duration import (
    IdlePeriodRequest,
    IdlePeriodResponse,
    IsoDurationResponse,
)
from jentic_one.shared.schemas import ServedApiRef
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE

PermissionStr = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_:./-]+$")]

_EX_AGENT_NAME = "invoice-reconciler"
_EX_AGENT_DESCRIPTION = "Reconciles Stripe invoices against the ledger every night."
_EX_PERMISSIONS = ["apis:read", "capabilities:execute"]
_EX_DENIAL_REASON = "Requested permissions exceed the agent's stated purpose."
_EX_CREDENTIAL_NAME = "Stripe production key"


_EX_EXPIRES_AT = "2026-12-31T23:59:00Z"


def _require_future(value: datetime | None) -> datetime | None:
    """Reject an ``expires_at`` that is not in the future (naive values read as UTC)."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if aware <= datetime.now(UTC):
        raise ValueError("expires_at must be in the future")
    return aware


_BINDING_EXPIRES_AT_DESCRIPTION = (
    "When the binding stops authorizing calls (RFC 3339, in the future). After this"
    " moment the broker refuses the agent's calls through it with 403"
    " `credential_binding_expired`, whatever its token. The binding keeps its permission"
    " rules; an owner renews it by setting a later time or null."
)

_BINDING_IDLE_WARNING_DESCRIPTION = WARNING_DESCRIPTION

_BINDING_QUOTA_DESCRIPTION = (
    "How many calls the binding allows before it ends (1 is single use); each authorized"
    " call takes one, whatever the upstream answers, and renewing restores the count."
)

_BINDING_IDLE_DESCRIPTION = (
    "Idle limit, as an ISO 8601 duration of weeks, days, hours, minutes or seconds (P1D"
    " to P999D, e.g. P14D; no years or months): the binding ends"
    " once it has gone this long without a call, and the broker then refuses calls"
    " through it with 403 `credential_binding_expired`. Each call restarts the clock"
    " (counted at most about hourly), and so does every renewal; a binding that is never"
    " used ends one idle period after it was granted."
)


class AgentResponse(BaseModel):
    """Agent representation in API responses."""

    id: str = Field(description="Unique ID of the agent.", examples=[ex.AGENT_ID])
    name: str = Field(description="Human-readable name of the agent.", examples=[_EX_AGENT_NAME])
    description: str | None = Field(
        default=None,
        description="Free-text description of the agent; null if not set.",
        examples=[_EX_AGENT_DESCRIPTION],
    )
    owner_id: str | None = Field(
        default=None,
        description="ID of the user who owns the agent; null for an unclaimed self-registration.",
        examples=[ex.USER_ID],
    )
    registered_by: str = Field(
        description="ID of the actor that registered the agent, or `self` for a self-registration.",
        examples=[ex.USER_ID],
    )
    parent_agent_id: str | None = Field(
        default=None,
        description="ID of the parent agent; null when the agent has no parent.",
        examples=[ex.AGENT_ID],
    )
    approved_by: str | None = Field(
        default=None,
        description="ID of the user who approved the agent; null if not approved.",
        examples=[ex.USER_ID],
    )
    status: str = Field(
        description=(
            "Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled`"
            " or `archived`."
        ),
        examples=["active"],
    )
    denial_reason: str | None = Field(
        default=None,
        description="Reason given when the agent was denied; null if not denied.",
        examples=[_EX_DENIAL_REASON],
    )
    denied_by: str | None = Field(
        default=None,
        description="ID of the actor that denied the agent; null if not denied.",
        examples=[ex.USER_ID],
    )
    created_at: datetime = Field(
        description="When the agent was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    approved_at: datetime | None = Field(
        default=None,
        description="When the agent was approved (RFC 3339); null if not approved.",
        examples=["2026-01-15T09:45:00Z"],
    )
    has_api_key: bool = Field(
        default=False,
        description="Whether the agent currently has an active API key.",
        examples=[True],
    )


class AgentListResponse(BaseModel):
    """List of agents."""

    data: list[AgentResponse] = Field(description="The page of agents.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )


class DenyRequest(BaseModel):
    """Request body for denying an agent."""

    reason: str = Field(
        min_length=1,
        max_length=1024,
        description="Why the agent is being denied.",
        examples=[_EX_DENIAL_REASON],
    )


class ClaimRequest(BaseModel):
    """Request body for claiming ownership of a self-registered agent."""

    # The single-use claim capability, presented once to take ownership. Marked
    # sensitive so the CLI's Layer-1 redactor masks it in output.
    token: str = Field(
        min_length=1,
        max_length=512,
        json_schema_extra=SENSITIVE,
        description="Single-use claim token returned when the agent self-registered.",
        examples=[ex.OPAQUE_TOKEN],
    )


class ApiLastUseResponse(BaseModel):
    """When an agent last called one API through a bound credential."""

    api_vendor: str = Field(description="Vendor of the API called.", examples=["github.com"])
    api_name: str | None = Field(
        default=None,
        description="Name of the API called; null when the recorded calls carry none.",
        examples=["github"],
    )
    api_version: str | None = Field(default=None, description="Recorded API version, when known.")
    last_used_at: datetime = Field(
        description="When the agent last called this API through the credential (RFC 3339).",
        examples=["2026-02-03T14:05:00Z"],
    )


class CredentialBindingResponse(BaseModel):
    """Direct agent↔credential binding representation in API responses."""

    id: str = Field(description="Unique ID of the binding.", examples=[ex.CREDENTIAL_BINDING_ID])
    agent_id: str = Field(
        description="ID of the agent the credential is bound to.", examples=[ex.AGENT_ID]
    )
    credential_id: str = Field(
        description="ID of the bound credential.", examples=[ex.CREDENTIAL_ID]
    )
    # Human-readable credential name (control DB); None when unresolvable.
    name: str | None = Field(
        default=None,
        description="Name of the bound credential; null if it cannot be resolved.",
        examples=[_EX_CREDENTIAL_NAME],
    )
    bound_at: datetime = Field(
        description="When the credential was bound (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    suspended: bool = Field(
        description="Whether the binding is suspended, cutting off the agent's access to it.",
        examples=[False],
    )
    # Why the binding is suspended: null for a manual suspension (the default
    # unbind), ``api_deleted`` when the API the credential serves was deleted.
    # Cleared when the binding is resumed.
    suspended_reason: str | None = Field(
        default=None,
        description=(
            "Why the binding is suspended: null for a manual suspension, `api_deleted`"
            " when the API the credential serves was deleted; cleared on resume."
        ),
        examples=["api_deleted"],
    )
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "When the binding stops authorizing calls (RFC 3339); null if it never expires."
        ),
        examples=[_EX_EXPIRES_AT],
    )
    expired: bool = Field(
        default=False,
        description="Whether `expires_at` has passed.",
        examples=[False],
    )
    expired_reason: GrantEndReason | None = Field(
        default=None,
        description=(
            "Which limit ended the binding while `expired` is true: `end_date`"
            " (`expires_at` passed), `idle` (unused for `max_idle_period`) or `uses`"
            " (no uses left). Null while the binding holds."
        ),
        examples=[None],
    )
    max_idle_period: IdlePeriodResponse | None = Field(
        default=None,
        description=_BINDING_IDLE_DESCRIPTION + " Null when it has no idle limit.",
        examples=["P14D"],
    )
    idle_expires_at: datetime | None = Field(
        default=None,
        description=(
            "When the idle limit ends the binding if it is not used before then (RFC 3339);"
            " null without an idle limit. Each use moves it later."
        ),
        examples=[_EX_EXPIRES_AT],
    )
    renewed_at: datetime | None = Field(
        default=None,
        description=(
            "When the binding was granted or last renewed (RFC 3339); the idle clock"
            " restarts at the later of this and its last use."
        ),
        examples=[_EX_EXPIRES_AT],
    )
    idle_warning: bool = Field(
        default=False,
        description=_BINDING_IDLE_WARNING_DESCRIPTION,
        examples=[False],
    )
    after_expiry: AfterExpiry = Field(
        default=AfterExpiry.HOLD,
        description=AFTER_EXPIRY_DESCRIPTION,
        examples=["hold"],
    )
    max_quota: int | None = Field(
        default=None,
        description=_BINDING_QUOTA_DESCRIPTION + " Null for unlimited calls.",
        examples=[1],
    )
    quota_remaining: int | None = Field(
        default=None,
        description="How many calls the binding still allows; null without a use count.",
        examples=[1],
    )
    # Shared permission rule set the binding points at (None = inline rules).
    rule_set_id: str | None = Field(
        default=None,
        description="ID of the shared permission rule set attached; null when inline rules apply.",
        examples=[ex.RULE_SET_ID],
    )
    serves: list[ServedApiRef] = Field(
        default=[], description="The APIs the bound credential serves; empty if unresolvable."
    )
    last_used_at: datetime | None = Field(
        default=None,
        description=(
            "When the agent last made a call through this credential, whatever its outcome"
            " (RFC 3339); null if it never has."
        ),
        examples=["2026-02-03T14:05:00Z"],
    )
    last_used_by_api: list[ApiLastUseResponse] = Field(
        default=[],
        description=(
            "`last_used_at` per API the agent called through this credential, most recent"
            " first; a vendor-wide credential has one entry per API used."
        ),
    )


class CredentialBindingListResponse(BaseModel):
    """List of direct credential bindings."""

    data: list[CredentialBindingResponse] = Field(
        description="The agent's direct credential bindings."
    )


class DurationLimitResponse(BaseModel):
    """A default and a maximum for one duration, as ISO 8601 durations; null when unset."""

    default: IsoDurationResponse | None = Field(
        default=None,
        description="Applied when a creation sets no policy; null for none.",
        examples=["P30D"],
    )
    max: IsoDurationResponse | None = Field(
        default=None,
        description="The longest allowed; when set, every grant (or agent) must have one.",
        examples=["P90D"],
    )


class GrantPolicyLimitsResponse(BaseModel):
    """The org's defaults and maximums for new grants."""

    expires_in: DurationLimitResponse = Field(
        description="A grant's end date, as a lifetime from when it is granted or renewed."
    )
    idle_period: DurationLimitResponse = Field(description="A grant's idle limit.")
    single_use_by_default: bool = Field(
        description="Whether a grant created with no policy allows a single call.",
        examples=[False],
    )


class EndingSoonBindingResponse(CredentialBindingResponse):
    """A binding whose end date or idle limit will end it soon."""

    agent_name: str = Field(
        description="Name of the agent the credential is bound to.", examples=[_EX_AGENT_NAME]
    )
    ends_at: datetime = Field(
        description="When the binding ends (RFC 3339): the nearer of its end date and its"
        " idle deadline, of those due a warning.",
        examples=[_EX_EXPIRES_AT],
    )
    ending: Literal["end_date", "idle"] = Field(
        description="Which limit ends it at `ends_at`: its end date (`end_date`) or its idle"
        " limit unless the agent uses it first (`idle`).",
        examples=["end_date"],
    )


class EndingSoonBindingListResponse(BaseModel):
    """Bindings that will soon reach their idle limit."""

    data: list[EndingSoonBindingResponse] = Field(
        description="Bindings with the warning on that end soon, soonest first."
    )


class AgentPatchRequest(BaseModel):
    """Request body for partially updating an agent."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New name for the agent.",
        examples=[_EX_AGENT_NAME],
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="New description for the agent.",
        examples=[_EX_AGENT_DESCRIPTION],
    )
    owner_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="ID of the user to transfer ownership to; `org:admin` only.",
        examples=[ex.USER_ID],
    )


class AgentCreateRequest(BaseModel):
    """Request body for creating an agent manually."""

    # Unknown keys are a 422: a body that spells the grant list ``scopes`` would
    # otherwise be ignored, leave ``permissions`` unset, and grant the full
    # default baseline.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable name of the agent.",
        examples=[_EX_AGENT_NAME],
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional free-text description of the agent.",
        examples=[_EX_AGENT_DESCRIPTION],
    )
    permissions: list[PermissionStr] | None = Field(
        default=None,
        max_length=100,
        description=(
            "Permissions to grant the agent; each must be grantable by the caller."
            " Omit to grant the default agent permission set."
        ),
        examples=[_EX_PERMISSIONS],
    )


class AgentPermissionsRequest(BaseModel):
    """Request body for replacing an agent's permissions."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[PermissionStr] = Field(
        max_length=100,
        description="The full set of permissions the agent should hold.",
        examples=[_EX_PERMISSIONS],
    )


class AgentPermissionsResponse(BaseModel):
    """Response containing an agent's current permissions."""

    permissions: list[str] = Field(
        description="Permissions currently granted to the agent.", examples=[_EX_PERMISSIONS]
    )


class ApiKeyResponse(BaseModel):
    """Response containing a plaintext API key (shown once)."""

    key: str = Field(
        description="The plaintext API key; shown only once, store it securely.",
        examples=["jak_example-secret"],  # pragma: allowlist secret
    )


class ApiKeyInfoResponse(BaseModel):
    """API key metadata — retrievable even after revocation."""

    id: str = Field(description="Unique ID of the API key record.", examples=[ex.AGENT_API_KEY_ID])
    status: str = Field(
        description="Key status: `active`, or `revoked` once revoked.", examples=["active"]
    )
    created_at: datetime = Field(
        description="When the API key was first created (RFC 3339).",
        examples=["2026-01-15T09:30:00Z"],
    )
    rotated_at: datetime | None = Field(
        default=None,
        description="When the API key was last rotated (RFC 3339); null if never rotated.",
        examples=["2026-01-20T14:00:00Z"],
    )
    created_by: str | None = Field(
        default=None,
        description="ID of the actor that created the API key.",
        examples=[ex.USER_ID],
    )


class ApiKeyHistoryEntryResponse(BaseModel):
    """A single event in the API key audit trail."""

    id: str = Field(description="Unique ID of the audit entry.", examples=[ex.AUDIT_ID])
    action: str = Field(
        description="Audited action, for example `rotate` or `revoke`.", examples=["rotate"]
    )
    reason: str | None = Field(
        default=None,
        description="Structured reason: `api_key_rotated` or `api_key_revoked`.",
        examples=["api_key_rotated"],
    )
    actor_id: str | None = Field(
        default=None,
        description="ID of the actor that performed the action.",
        examples=[ex.USER_ID],
    )
    occurred_at: datetime = Field(
        description="When the action occurred (RFC 3339).", examples=["2026-01-20T14:00:00Z"]
    )


class ApiKeyHistoryResponse(BaseModel):
    """Audit trail of API key operations."""

    data: list[ApiKeyHistoryEntryResponse] = Field(description="API key events, newest first.")


class CredentialBindingPatchRequest(BaseModel):
    """Request body for renewing a credential binding: re-certify it, and change its limits.

    Every renewal restarts the binding's idle clock. Each limit sent is set, and
    null clears it; an absent field is never read as clearing. An end date left
    out runs the binding again for as long as it was granted for, capped at the
    org's maximum; any other limit left out stays as it is. An empty body
    re-certifies ("still needed").
    """

    model_config = ConfigDict(extra="forbid")

    expires_at: datetime | None = Field(
        default=None,
        description=(
            _BINDING_EXPIRES_AT_DESCRIPTION
            + " Send null to clear the end date. Left out, the binding runs again for as long"
            " as it was granted for (its end date less when it was granted or last renewed),"
            " capped at the org's maximum; one with no end date takes the maximum, or keeps"
            " none without one."
        ),
        examples=[_EX_EXPIRES_AT],
    )
    max_idle_period: IdlePeriodRequest | None = Field(
        default=None,
        description=(
            _BINDING_IDLE_DESCRIPTION
            + " Send null to remove the idle limit; leave it out to keep it."
        ),
        examples=["P14D"],
    )
    idle_warning: bool | None = Field(
        default=None,
        description=_BINDING_IDLE_WARNING_DESCRIPTION + " Leave it out to keep it as it is.",
        examples=[True],
    )
    after_expiry: AfterExpiry | None = Field(
        default=None,
        description=AFTER_EXPIRY_DESCRIPTION + " Leave it out to keep it as it is.",
        examples=["refuse"],
    )
    max_quota: int | None = Field(
        default=None,
        ge=1,
        le=MAX_QUOTA,
        description=(
            _BINDING_QUOTA_DESCRIPTION
            + " Send null to remove the count; leave it out to keep it (every renewal"
            " restores the uses left)."
        ),
        examples=[1],
    )

    _check_expires_at = field_validator("expires_at")(_require_future)


class CredentialBindRequest(BaseModel):
    """Request body for directly binding a credential to an agent."""

    credential_id: str = Field(
        min_length=1,
        max_length=255,
        description="ID of the credential to bind to the agent.",
        examples=[ex.CREDENTIAL_ID],
    )
    expires_at: datetime | None = Field(
        default=None,
        description=_BINDING_EXPIRES_AT_DESCRIPTION
        + " Null for a binding that never expires (refused under an org maximum). A body"
        " that sets none of `expires_at`, `max_idle_period` and `max_quota` gets the org's"
        " default policy (`GET /agents/credentials:policy`; none unless the org sets one).",
        examples=[_EX_EXPIRES_AT],
    )
    max_idle_period: IdlePeriodRequest | None = Field(
        default=None,
        description=_BINDING_IDLE_DESCRIPTION + " Omit for no idle limit.",
        examples=["P14D"],
    )
    idle_warning: bool = Field(
        default=False,
        description=_BINDING_IDLE_WARNING_DESCRIPTION,
        examples=[False],
    )
    after_expiry: AfterExpiry = Field(
        default=AfterExpiry.HOLD,
        description=AFTER_EXPIRY_DESCRIPTION,
        examples=["hold"],
    )
    max_quota: int | None = Field(
        default=None,
        ge=1,
        le=MAX_QUOTA,
        description=_BINDING_QUOTA_DESCRIPTION + " Omit for unlimited calls.",
        examples=[1],
    )

    _check_expires_at = field_validator("expires_at")(_require_future)


class JwksUpdateRequest(BaseModel):
    """Request body for updating an agent's JWKS (public keys).

    The JWKS must contain at least one Ed25519 public key and must not
    contain any private key material.
    """

    jwks: dict[str, Any] = Field(
        description="JWKS containing public keys",
        examples=[
            {
                "keys": [
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        # RFC 8037 appendix A.2 public-key test vector (not a
                        # secret).
                        # pragma: allowlist nextline secret
                        "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
                        "kid": "agent-key-1",
                    }
                ]
            }
        ],
    )
