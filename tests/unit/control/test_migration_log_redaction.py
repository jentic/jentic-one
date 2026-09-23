"""The migration jobs' structured-log fields survive the central redactor.

``shared/redaction.py`` blanks any key containing ``secret``, ``credential``,
or ``_token``. The SA-migration and key-retirement report fields
(``had_client_secret``, ``credential_binding_count``,
``access_tokens_revoked``, ``bound_credential_ids`` …) carry no secret but
match those substrings, so the log lines rename them (the redaction rule's
prescribed fix) — these tests pin that the operator signal (OQ-1: who still
holds client_credentials) reaches the log intact, while the JSONL report keeps
the dataclass field names.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from jentic_one.control.services import key_retirement, service_account_migration
from jentic_one.control.services.key_retirement import KeyRetirementOutcome
from jentic_one.control.services.service_account_migration import (
    ServiceAccountMigrationOutcome,
)
from jentic_one.shared.redaction import REDACTED, redact_event

_SA_OUTCOME = ServiceAccountMigrationOutcome(
    service_account_id="sva_1",
    outcome="migrated",
    successor_agent_id="agnt_1",
    stored_permission_count=3,
    toolkit_binding_count=1,
    credential_binding_count=2,
    permission_rule_count=4,
    access_tokens_revoked=5,
    refresh_tokens_revoked=6,
    had_client_secret=True,
    owner_visibility_note="note",
    reason="why",
)

_KEY_OUTCOME = KeyRetirementOutcome(
    key_id="ck_1",
    toolkit_id="tk_1",
    toolkit_name="tk",
    action="migrated",
    successor_actor_id="agnt_1",
    bound_credential_ids=("cred_a", "cred_b"),
    rule_less_credential_ids=("cred_b",),
)


def _redacted_keys(event: dict[str, object]) -> list[str]:
    return [k for k, v in redact_event(None, "info", event).items() if v == REDACTED]


def test_raw_report_fields_would_be_redacted() -> None:
    """Guard the premise: the unrenamed fields DO trip the redactor."""
    assert set(_redacted_keys(asdict(_SA_OUTCOME))) == {
        "had_client_secret",
        "credential_binding_count",
        "access_tokens_revoked",
        "refresh_tokens_revoked",
    }
    assert set(_redacted_keys(asdict(_KEY_OUTCOME))) == {
        "bound_credential_ids",
        "rule_less_credential_ids",
    }


def test_sa_migration_log_fields_survive_redaction() -> None:
    fields = service_account_migration._log_fields(asdict(_SA_OUTCOME))
    out = redact_event(None, "info", fields)

    assert REDACTED not in out.values()
    assert out["cc_holder"] is True
    assert out["cred_binding_count"] == 2
    assert out["access_revoked_count"] == 5
    assert out["refresh_revoked_count"] == 6
    assert len(out) == len(asdict(_SA_OUTCOME))  # renamed, never dropped


def test_key_retirement_log_fields_survive_redaction() -> None:
    out = redact_event(None, "info", key_retirement._log_fields(_KEY_OUTCOME))

    assert REDACTED not in out.values()
    assert out["bound_cred_ids"] == ("cred_a", "cred_b")
    assert out["rule_less_cred_ids"] == ("cred_b",)


@pytest.mark.parametrize(
    "key",
    [
        *service_account_migration._LOG_KEY_RENAMES.values(),
        *key_retirement._LOG_KEY_RENAMES.values(),
    ],
)
def test_renamed_log_keys_are_not_sensitive(key: str) -> None:
    """Every log-side name (incl. the sweep/verify lines') passes untouched."""
    assert redact_event(None, "info", {key: 1}) == {key: 1}


def test_jsonl_report_field_names_unchanged() -> None:
    """The report (not redacted) keeps the stable dataclass field names."""
    assert {"had_client_secret", "credential_binding_count"} <= asdict(_SA_OUTCOME).keys()
    assert "bound_credential_ids" in asdict(_KEY_OUTCOME)
