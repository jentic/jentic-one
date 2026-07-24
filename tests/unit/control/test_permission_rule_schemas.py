"""Unit tests for the shared permission-rule base schema.

Both authoring surfaces (`toolkits.py` and `access_requests.py`) inherit
from :class:`BasePermissionRuleSchema`, so validation is exercised through
the concrete subclasses here — the goal is to prove save-time behaviour is
identical on both surfaces.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jentic_one.control.web.schemas.access_requests import (
    PermissionRuleSchema as ARPermissionRuleSchema,
)
from jentic_one.control.web.schemas.toolkits import (
    PermissionRuleSchema as TKPermissionRuleSchema,
)

# ---------------------------------------------------------------------------
# match_mode default + acceptance
# ---------------------------------------------------------------------------


def test_toolkit_rule_defaults_match_mode_to_regex() -> None:
    rule = TKPermissionRuleSchema(effect="allow", path=".*")
    assert rule.match_mode == "regex"


@pytest.mark.parametrize("mode", ["regex", "prefix", "exact"])
def test_toolkit_rule_accepts_all_match_modes(mode: str) -> None:
    rule = TKPermissionRuleSchema(effect="allow", path="/v1/x", match_mode=mode)  # type: ignore[arg-type]
    assert rule.match_mode == mode


def test_toolkit_rule_rejects_unknown_match_mode() -> None:
    with pytest.raises(ValidationError):
        TKPermissionRuleSchema.model_validate(
            {"effect": "allow", "path": "/x", "match_mode": "glob"}
        )


# ---------------------------------------------------------------------------
# Path validation (delegates to shared seam, surfaces `re.error` reason)
# ---------------------------------------------------------------------------


def test_toolkit_rule_rejects_invalid_regex_with_reason() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TKPermissionRuleSchema(effect="allow", path="[unterminated", match_mode="regex")
    # The reason text carries the underlying ``re.error`` so callers can fix
    # the pattern without guessing what tripped validation.
    assert "invalid regex" in str(exc_info.value).lower()


def test_toolkit_rule_rejects_oversized_path() -> None:
    with pytest.raises(ValidationError):
        TKPermissionRuleSchema(effect="allow", path="a" * 1001)


@pytest.mark.parametrize("mode", ["regex", "prefix", "exact"])
def test_toolkit_rule_rejects_empty_path(mode: str) -> None:
    # An empty string satisfies the truthiness of "field is set" (bypassing
    # the condition-less-allow guard) yet matches every request in prefix
    # mode. The seam rejects it before the guard fires.
    with pytest.raises(ValidationError):
        TKPermissionRuleSchema.model_validate({"effect": "allow", "path": "", "match_mode": mode})


# ---------------------------------------------------------------------------
# Condition-less-`allow` guard (shared across surfaces)
# ---------------------------------------------------------------------------


def test_toolkit_condition_less_allow_still_rejected() -> None:
    # Guard predates #751 but must survive the refactor onto the shared base.
    with pytest.raises(ValidationError):
        TKPermissionRuleSchema(effect="allow")


def test_toolkit_condition_less_deny_stays_valid() -> None:
    rule = TKPermissionRuleSchema(effect="deny")
    assert rule.effect == "deny"


def test_access_request_condition_less_allow_still_rejected() -> None:
    with pytest.raises(ValidationError):
        ARPermissionRuleSchema(effect="allow")


def test_access_request_require_approval_condition_less_stays_valid() -> None:
    # ``require-approval`` (access-request-only) is a legitimate catch-all
    # like ``deny``, so the guard must not fire on it.
    rule = ARPermissionRuleSchema(effect="require-approval")
    assert rule.effect == "require-approval"


# ---------------------------------------------------------------------------
# extra="forbid" — misspelled fields fail loud, both surfaces
# ---------------------------------------------------------------------------


def test_toolkit_rule_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TKPermissionRuleSchema.model_validate({"effect": "allow", "mach_mode": "regex"})


def test_access_request_rule_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ARPermissionRuleSchema.model_validate({"effect": "allow", "mach_mode": "regex"})


# ---------------------------------------------------------------------------
# Dump semantics — match_mode always survives model_dump(exclude_none=True)
# ---------------------------------------------------------------------------


def test_toolkit_rule_dump_always_carries_match_mode() -> None:
    # ``match_mode`` has a non-None default so ``exclude_none=True`` never
    # drops it — the repo layer can always trust ``rule_data["match_mode"]``.
    rule = TKPermissionRuleSchema(effect="allow", path=".*")
    dumped = rule.model_dump(exclude_none=True)
    assert dumped["match_mode"] == "regex"
