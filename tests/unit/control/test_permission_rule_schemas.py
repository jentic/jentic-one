"""Unit tests for the shared permission-rule base schema.

The credentials API (`permission_rules.py`) inherits from
:class:`BasePermissionRuleSchema`; validation is exercised through the
concrete subclass here to prove save-time behaviour. (The access-request
API was a second consumer until theme 7 removed it.)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jentic_one.control.web.schemas.permission_rules import (
    PermissionRuleSchema as CredPermissionRuleSchema,
)

# ---------------------------------------------------------------------------
# match_mode default + acceptance
# ---------------------------------------------------------------------------


def test_credential_rule_defaults_match_mode_to_regex() -> None:
    rule = CredPermissionRuleSchema(effect="allow", path=".*")
    assert rule.match_mode == "regex"


@pytest.mark.parametrize("mode", ["regex", "prefix", "exact"])
def test_credential_rule_accepts_all_match_modes(mode: str) -> None:
    rule = CredPermissionRuleSchema(effect="allow", path="/v1/x", match_mode=mode)  # type: ignore[arg-type]
    assert rule.match_mode == mode


def test_credential_rule_rejects_unknown_match_mode() -> None:
    with pytest.raises(ValidationError):
        CredPermissionRuleSchema.model_validate(
            {"effect": "allow", "path": "/x", "match_mode": "glob"}
        )


# ---------------------------------------------------------------------------
# Path validation (delegates to shared seam, surfaces `re.error` reason)
# ---------------------------------------------------------------------------


def test_credential_rule_rejects_invalid_regex_with_reason() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CredPermissionRuleSchema(effect="allow", path="[unterminated", match_mode="regex")
    # The reason text carries the underlying ``re.error`` so callers can fix
    # the pattern without guessing what tripped validation.
    assert "invalid regex" in str(exc_info.value).lower()


def test_credential_rule_rejects_oversized_path() -> None:
    with pytest.raises(ValidationError):
        CredPermissionRuleSchema(effect="allow", path="a" * 1001)


@pytest.mark.parametrize("mode", ["regex", "prefix", "exact"])
def test_credential_rule_rejects_empty_path(mode: str) -> None:
    # An empty string satisfies the truthiness of "field is set" (bypassing
    # the condition-less-allow guard) yet matches every request in prefix
    # mode. The seam rejects it before the guard fires.
    with pytest.raises(ValidationError):
        CredPermissionRuleSchema.model_validate({"effect": "allow", "path": "", "match_mode": mode})


# ---------------------------------------------------------------------------
# Condition-less-`allow` guard
# ---------------------------------------------------------------------------


def test_credential_condition_less_allow_still_rejected() -> None:
    # A condition-less ``allow`` must be rejected by the shared base (#751).
    with pytest.raises(ValidationError):
        CredPermissionRuleSchema(effect="allow")


def test_credential_condition_less_deny_stays_valid() -> None:
    rule = CredPermissionRuleSchema(effect="deny")
    assert rule.effect == "deny"


# ---------------------------------------------------------------------------
# extra="forbid" — misspelled fields fail loud
# ---------------------------------------------------------------------------


def test_credential_rule_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CredPermissionRuleSchema.model_validate({"effect": "allow", "mach_mode": "regex"})


# ---------------------------------------------------------------------------
# Dump semantics — match_mode always survives model_dump(exclude_none=True)
# ---------------------------------------------------------------------------


def test_credential_rule_dump_always_carries_match_mode() -> None:
    # ``match_mode`` has a non-None default so ``exclude_none=True`` never
    # drops it — the repo layer can always trust ``rule_data["match_mode"]``.
    rule = CredPermissionRuleSchema(effect="allow", path=".*")
    dumped = rule.model_dump(exclude_none=True)
    assert dumped["match_mode"] == "regex"
