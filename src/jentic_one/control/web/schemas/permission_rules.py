"""Permission-rule web schemas for the credentials authoring surface.

The base shape — including save-time path validation and the
condition-less-``allow`` guard — is split from the concrete allow/deny
subclass so every authoring surface that writes agent↔credential permission
rules shares one set of field semantics. (The access-request API, removed in
theme 7, was the second consumer; the split survives it.)

The concrete allow/deny subclass and the read/patch/test models live here
too (consumed by ``routers/credentials.py`` and ``schemas/credentials.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from jentic_one.shared.permissions.matching import MatchMode, validate_path
from jentic_one.shared.web import openapi_examples as ex


class BasePermissionRuleSchema(BaseModel):
    """Shared fields + validation for both permission-rule authoring schemas.

    ``extra="forbid"`` catches misspelled fields (e.g. ``mach_mode``) with
    a ``422`` instead of the request silently ignoring them.
    """

    model_config = ConfigDict(extra="forbid")

    methods: list[str] | None = Field(
        default=None,
        examples=[["GET"]],
        description="HTTP methods to match (case-insensitive). None matches all.",
    )
    path: str | None = Field(
        default=None,
        examples=["/v1/charges"],
        description=(
            "Path pattern to match, relative to the API's server URL — the "
            "spec's path (`/widgets`), never the full upstream path with the "
            "server's base path (`/eu/widgets` for a server `https://host/{region}`). "
            "Interpreted per `match_mode`: `regex` uses "
            "full-match semantics (the pattern must describe the whole path); "
            "`prefix` and `exact` are literal. None matches all paths."
        ),
    )
    match_mode: MatchMode = Field(
        default="regex",
        examples=["prefix"],
        description=(
            "How `path` is interpreted: `regex` (full-match), `prefix` "
            "(string prefix), or `exact` (equality). Defaults to `regex` for "
            "backwards compatibility."
        ),
    )
    operations: list[str] | None = Field(
        default=None,
        examples=[[ex.OPERATION_ID]],
        description="OpenAPI operation IDs to match. None matches all operations.",
    )
    comment: str | None = Field(
        default=None,
        max_length=500,
        # Also ``_comment``, the name reads return it under, so a rule read
        # back can be sent as is.
        validation_alias=AliasChoices("comment", "_comment"),
        examples=["Lets the agent draft replies, never send them."],
        description="Why the rule is there, in words. Shown wherever the rule is listed.",
    )

    @model_validator(mode="after")
    def _validate_path(self) -> BasePermissionRuleSchema:
        # Surface the underlying ``re.error`` (or length/empty/unknown-mode
        # reason) as the ``ValueError`` message; FastAPI renders it as ``422``
        # with the reason in the ``detail``, so callers can fix the pattern
        # without guessing what tripped validation.
        err = validate_path(self.path, self.match_mode)
        if err is not None:
            raise ValueError(err.reason)
        return self

    @model_validator(mode="after")
    def _reject_condition_less_allow(self) -> BasePermissionRuleSchema:
        # A condition-less ``allow`` matches every request under the broker's
        # first-match-wins evaluation — an unrestricted grant. Reject it so a
        # binding can never grant blanket access by accident. A condition-less
        # ``require-approval`` likewise holds every request without constraint,
        # which creates an operator bottleneck; require at least one condition
        # for both effects. Empty-string ``path`` cannot slip past here because
        # ``_validate_path`` above already rejects it in every mode.
        effect = getattr(self, "effect", None)
        if effect in ("allow", "require-approval") and not (
            self.methods or self.path or self.operations
        ):
            msg = f"A '{effect}' rule must constrain at least one of methods, path, or operations"
            raise ValueError(msg)
        return self


class PermissionRuleSchema(BasePermissionRuleSchema):
    """Permission rule for an agent↔credential binding.

    Rules are evaluated first-match-wins. If no rule matches, the request is
    denied (default-deny). A binding with zero rules therefore blocks all
    operations — users must explicitly add at least one allow rule.
    """

    effect: Literal["allow", "deny", "require-approval"] = Field(
        description=(
            "Whether this rule allows the matched request, denies it, "
            "or holds it for human approval before the broker executes it."
        ),
        examples=["allow"],
    )


class PermissionRuleReadSchema(BaseModel):
    """Permission rule response (includes system fields)."""

    id: str | None = Field(
        default=None,
        description=(
            "ID of the rule (`apr_…` on a binding, `prr_…` in a rule set), so a connect-session "
            "edit can name it in `remove` or `before`."
        ),
        examples=[ex.PERMISSION_RULE_ID],
    )
    effect: Literal["allow", "deny", "require-approval"] = Field(
        description="Whether this rule allows, denies, or holds the matched request for "
        "human approval (`require-approval`).",
        examples=["allow"],
    )
    methods: list[str] | None = Field(
        default=None,
        description="HTTP methods the rule matches; null matches all.",
        examples=[["GET"]],
    )
    path: str | None = Field(
        default=None,
        examples=["/v1/charges"],
        description=(
            "Path pattern relative to the API's server URL, interpreted per `match_mode`; "
            "null matches all paths."
        ),
    )
    match_mode: MatchMode = Field(
        default="regex",
        examples=["prefix"],
        description="How `path` is interpreted: `regex` (full-match), `prefix` or `exact`.",
    )
    operations: list[str] | None = Field(
        default=None,
        description="OpenAPI operation IDs the rule matches; null matches all.",
        examples=[[ex.OPERATION_ID]],
    )
    is_system: bool = Field(
        alias="_system",
        default=False,
        description="True when the rule is platform-managed rather than user-authored.",
        examples=[False],
    )
    comment: str | None = Field(
        alias="_comment",
        default=None,
        description="Optional note attached to the rule.",
        examples=["Read-only access to charges."],
    )

    model_config = {"populate_by_name": True}


class PermissionsPatchRequest(BaseModel):
    """Patch permission rules — add and/or remove."""

    add: list[PermissionRuleSchema] | None = Field(
        default=None, description="Rules to append after the existing user rules."
    )
    remove: list[int] | None = Field(
        default=None,
        examples=[[0, 2]],
        description=(
            "Zero-based indexes of user rules (excluding system rules) to delete; "
            "applied before `add`."
        ),
    )


class PermissionRuleListResponse(BaseModel):
    """List of permission rules."""

    data: list[PermissionRuleReadSchema] = Field(
        description="The binding's permission rules, in evaluation order."
    )


class BindingRuleSetSchema(BaseModel):
    """The shared rule set a binding points at."""

    id: str = Field(description="ID of the rule set (`prs_…`).", examples=[ex.RULE_SET_ID])
    name: str = Field(description="Name of the rule set.", examples=["Stripe read-only"])
    curated: bool = Field(
        description="True for a curated set, which an edit can copy but never change.",
        examples=[False],
    )
    rules: list[PermissionRuleReadSchema] = Field(
        description="The set's rules, in evaluation order. They apply instead of `data`."
    )


class ScopeOptionSchema(BaseModel):
    """One scope the credential's vendor offers, which an edit can add or remove."""

    name: str = Field(description="The scope as the vendor names it.", examples=["gmail.send"])
    classification: Literal["read", "write", "admin"] = Field(
        description="What the scope lets a holder do.", examples=["write"]
    )
    description: str = Field(
        description="What the scope grants, in words.", examples=["Send email as the user."]
    )


class BindingAccessResponse(PermissionRuleListResponse):
    """A binding's permission rules plus the rest of what it grants."""

    rule_set: BindingRuleSetSchema | None = Field(
        default=None,
        description=(
            "The shared rule set the binding points at; null when its own rules (`data`) apply. "
            "While a set is attached, `data` is dormant."
        ),
    )
    granted_scopes: list[str] | None = Field(
        default=None,
        description="Scopes the credential's OAuth grant holds; null when it has none.",
        examples=[["repo", "read:user"]],
    )
    suspended: bool = Field(
        description="True when the binding is suspended and the agent cannot use it.",
        examples=[False],
    )
    scopes_editable: bool = Field(
        default=False,
        description=(
            "True when an edit can change the credential's OAuth scopes (add or remove); "
            "the approver signs in to the vendor again for the new set."
        ),
        examples=[True],
    )
    scope_catalog: list[ScopeOptionSchema] | None = Field(
        default=None,
        description=(
            "The scopes the credential's vendor offers; an edit can add only these. Null when "
            "its client has no catalog, in which case a scope is passed to the vendor as named."
        ),
    )


class PermissionTestRequest(BaseModel):
    """Request body for :test — dry-run a request shape against the binding's rules."""

    model_config = ConfigDict(extra="forbid")

    method: str = Field(
        description="HTTP method of the hypothetical request (case-insensitive).",
        examples=["GET"],
    )
    path: str = Field(
        examples=["/v1/charges"],
        description=(
            "Path of the hypothetical request relative to the API's server URL "
            "(the spec's path, e.g. `/widgets/42`) — the path the broker "
            "evaluates rules on."
        ),
    )
    operation_id: str | None = Field(
        default=None,
        description="Optional OpenAPI operation id resolved from the request URL.",
        examples=[ex.OPERATION_ID],
    )


class PermissionTestResponse(BaseModel):
    """Dry-run result matching :class:`PermissionTestResult`."""

    allowed: bool = Field(
        description="Whether the broker would allow this request under the binding's rules.",
        examples=[True],
    )
    matched: bool = Field(
        description="Whether any rule matched; when false, the outcome is default-deny.",
        examples=[True],
    )
    effect: str | None = Field(
        default=None,
        examples=["allow"],
        description="Effect of the matching rule (`allow`/`deny`); null when no match.",
    )
    rule_index: int | None = Field(
        default=None,
        examples=[0],
        description="Zero-based index in the binding's ordered rule list; null when no match.",
    )
    credential_id: str | None = Field(
        default=None,
        examples=[ex.CREDENTIAL_ID],
        description="The binding whose rule list contributed the matching rule.",
    )
    is_system: bool | None = Field(
        default=None,
        examples=[False],
        description="True when the matching rule was written by the system; null when no match.",
    )
