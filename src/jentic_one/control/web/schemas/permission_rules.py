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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jentic_one.shared.permissions.api_roles import ROLE_MATCH_MODES, ApiRoleName
from jentic_one.shared.permissions.git_rules import GIT_MATCH_MODE
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
            "`prefix` and `exact` are literal. None matches all paths. With "
            "`match_mode` `graphql`, `path` is required and is a GraphQL selector "
            "instead: `query`, `mutation`, or `mutation:createPullRequest,addComment` "
            "(top-level field names, never aliases). With `match_mode` `git`, `path` "
            "is required and is a git selector: `<fetch|push|force>:<owner>/<repo>` "
            "with an optional `:refs/heads/<glob>` or `:refs/tags/<glob>` on push "
            "and force (e.g. `push:octo-org/app:refs/heads/agent/*`)."
        ),
    )
    match_mode: MatchMode = Field(
        default="regex",
        examples=["prefix"],
        description=(
            "How `path` is interpreted: `regex` (full-match), `prefix` "
            "(string prefix), or `exact` (equality). Defaults to `regex` for "
            "backwards compatibility. `graphql` makes the rule match the top-level "
            "fields of a GraphQL request (the GitHub GraphQL endpoint) by operation "
            "type and field name, one field at a time; it matches no other request. "
            "`git` makes the rule match git clones, fetches and pushes to github.com "
            "through Jentic by repository and, for pushes, by ref; it matches no other "
            "request, and only `allow` and `deny` are accepted."
        ),
    )
    operations: list[str] | None = Field(
        default=None,
        examples=[[ex.OPERATION_ID]],
        description="OpenAPI operation IDs to match. None matches all operations.",
    )
    api_role: ApiRoleName | None = Field(
        default=None,
        examples=["rest"],
        description=(
            "On the binding of a CLI-profile credential (one connect covering several APIs "
            "of a vendor, e.g. `jentic connect github`): the profile API the rule applies "
            "to — `rest` and `uploads` take path rules, `graphql` GraphQL rules and `git` "
            "git rules. Omitted, the rule's kind decides: a `git` rule applies to `git`, a "
            "`graphql` rule to `graphql`, any other rule to `rest`. Ignored on any other "
            "credential."
        ),
    )

    @model_validator(mode="after")
    def _validate_api_role(self) -> BasePermissionRuleSchema:
        if self.api_role is not None and self.match_mode not in ROLE_MATCH_MODES[self.api_role]:
            allowed = ", ".join(sorted(ROLE_MATCH_MODES[self.api_role]))
            raise ValueError(
                f"A rule for the '{self.api_role}' API takes match_mode {allowed}, "
                f"not '{self.match_mode}'"
            )
        return self

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
    def _reject_held_git_rule(self) -> BasePermissionRuleSchema:
        # A held git push would block git with no output past an agent's tool
        # timeout, so git rules allow or deny only (``shared.permissions.git_rules``).
        if self.match_mode != GIT_MATCH_MODE:
            return self
        if getattr(self, "effect", None) == "require-approval":
            raise ValueError("A 'git' rule's effect must be 'allow' or 'deny'")
        # A git request has no HTTP method or registry operation a rule could
        # match, so a git rule constraining either would never match.
        if self.methods or self.operations:
            raise ValueError(
                "A 'git' rule takes no methods or operations: its selector in 'path' "
                "names the git action, repository and ref"
            )
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
            "null matches all paths. A GraphQL selector when `match_mode` is `graphql`, "
            "a git selector when it is `git`."
        ),
    )
    match_mode: MatchMode = Field(
        default="regex",
        examples=["prefix"],
        description=(
            "How `path` is interpreted: `regex` (full-match), `prefix`, `exact`, "
            "`graphql` (a GraphQL top-level-field selector) or `git` (a git "
            "repository/ref selector)."
        ),
    )
    operations: list[str] | None = Field(
        default=None,
        description="OpenAPI operation IDs the rule matches; null matches all.",
        examples=[[ex.OPERATION_ID]],
    )
    api_role: ApiRoleName | None = Field(
        default=None,
        examples=["rest"],
        description=(
            "The CLI-profile API the rule applies to on a CLI-profile credential's binding; "
            "null takes the role the rule's kind implies (`git`, `graphql`, else `rest`)."
        ),
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
