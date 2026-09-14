"""Permission-rule web schemas shared across authoring surfaces.

Two authoring surfaces write agent↔credential permission rules: the
credentials API (allow / deny) and the access-request API (which
additionally accepts ``require-approval`` on filed items). They share every
field except ``effect``, so the common shape — including save-time path
validation and the condition-less-``allow`` guard — lives here to prevent
the two surfaces from drifting.

The concrete allow/deny subclass and the read/patch/test models live here
too (consumed by ``routers/credentials.py`` and ``schemas/credentials.py``);
``access_requests.py`` adds its own ``effect`` ``Literal``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jentic_one.shared.permissions.matching import MatchMode, validate_path


class BasePermissionRuleSchema(BaseModel):
    """Shared fields + validation for both permission-rule authoring schemas.

    ``extra="forbid"`` catches misspelled fields (e.g. ``mach_mode``) with
    a ``422`` instead of the request silently ignoring them.
    """

    model_config = ConfigDict(extra="forbid")

    methods: list[str] | None = Field(
        default=None,
        description="HTTP methods to match (case-insensitive). None matches all.",
    )
    path: str | None = Field(
        default=None,
        description=(
            "Path pattern to match. Interpreted per `match_mode`: `regex` uses "
            "full-match semantics (the pattern must describe the whole path); "
            "`prefix` and `exact` are literal. None matches all paths."
        ),
    )
    match_mode: MatchMode = Field(
        default="regex",
        description=(
            "How `path` is interpreted: `regex` (full-match), `prefix` "
            "(string prefix), or `exact` (equality). Defaults to `regex` for "
            "backwards compatibility."
        ),
    )
    operations: list[str] | None = Field(
        default=None,
        description="OpenAPI operation IDs to match. None matches all operations.",
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
        # binding can never grant blanket access by accident. Empty-string
        # ``path`` cannot slip past here because ``_validate_path`` above
        # already rejects it in every mode.
        effect = getattr(self, "effect", None)
        if effect == "allow" and not (self.methods or self.path or self.operations):
            msg = "An 'allow' rule must constrain at least one of methods, path, or operations"
            raise ValueError(msg)
        return self


class PermissionRuleSchema(BasePermissionRuleSchema):
    """Permission rule for an agent↔credential binding.

    Rules are evaluated first-match-wins. If no rule matches, the request is
    denied (default-deny). A binding with zero rules therefore blocks all
    operations — users must explicitly add at least one allow rule.
    """

    effect: Literal["allow", "deny"] = Field(
        description="Whether this rule allows or denies the matched request."
    )


class PermissionRuleReadSchema(BaseModel):
    """Permission rule response (includes system fields)."""

    effect: Literal["allow", "deny"]
    methods: list[str] | None = None
    path: str | None = None
    match_mode: MatchMode = "regex"
    operations: list[str] | None = None
    is_system: bool = Field(alias="_system", default=False)
    comment: str | None = Field(alias="_comment", default=None)

    model_config = {"populate_by_name": True}


class PermissionsPatchRequest(BaseModel):
    """Patch permission rules — add and/or remove."""

    add: list[PermissionRuleSchema] | None = None
    remove: list[int] | None = None


class PermissionRuleListResponse(BaseModel):
    """List of permission rules."""

    data: list[PermissionRuleReadSchema]


class PermissionTestRequest(BaseModel):
    """Request body for :test — dry-run a request shape against the binding's rules."""

    model_config = ConfigDict(extra="forbid")

    method: str = Field(
        description="HTTP method of the hypothetical request (case-insensitive).",
    )
    path: str = Field(
        description="Path of the hypothetical request as the broker would see it.",
    )
    operation_id: str | None = Field(
        default=None,
        description="Optional OpenAPI operation id resolved from the request URL.",
    )


class PermissionTestResponse(BaseModel):
    """Dry-run result matching :class:`PermissionTestResult`."""

    allowed: bool = Field(
        description="Whether the broker would allow this request under the binding's rules."
    )
    matched: bool = Field(
        description="Whether any rule matched; when false, the outcome is default-deny."
    )
    effect: str | None = Field(
        default=None,
        description="Effect of the matching rule (`allow`/`deny`); null when no match.",
    )
    rule_index: int | None = Field(
        default=None,
        description="Zero-based index in the binding's ordered rule list; null when no match.",
    )
    credential_id: str | None = Field(
        default=None,
        description="The binding whose rule list contributed the matching rule.",
    )
    is_system: bool | None = Field(
        default=None,
        description="True when the matching rule was written by the system; null when no match.",
    )
