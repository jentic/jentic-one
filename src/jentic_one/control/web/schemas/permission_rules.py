"""Shared base for permission-rule schemas.

Two authoring surfaces write into the same enforced table
(``toolkit_permission_rules``): the toolkit-bindings API (allow / deny)
and the access-request API (which additionally accepts ``require-approval``
on filed items). They share every field except ``effect``, so the common
shape — including save-time path validation and the
condition-less-``allow`` guard — lives here to prevent the two surfaces
from drifting.

Concrete subclasses in ``toolkits.py`` and ``access_requests.py`` add the
appropriate ``effect`` ``Literal``.
"""

from __future__ import annotations

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
