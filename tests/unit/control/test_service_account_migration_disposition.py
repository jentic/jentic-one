"""Unit tests for the theme-8 service-account retirement's pure logic.

T-1: the OQ-1 switch — ``active`` → migrate, ``disabled`` → migrate-disabled,
``pending``/``rejected``/``archived`` → skip-but-stamp. Phase 4: the
inline-rule parity check and the refusal error's shape. Pure logic only — the
copy/verify/sweep transactions are integration-tested against real databases
(no DB mocking).
"""

from __future__ import annotations

import pytest

from jentic_one.control.services.service_account_migration import (
    RetirementProblem,
    ServiceAccountMigrationService,
    ServiceAccountRetirementError,
    rule_parity_problems,
    rule_parity_warnings,
)
from jentic_one.shared.models import ActorStatus


@pytest.mark.parametrize(
    ("status", "label", "successor_status"),
    [
        ("active", "migrated", ActorStatus.ACTIVE.value),
        ("disabled", "migrated-disabled", ActorStatus.DISABLED.value),
        ("pending", "skipped-non-active", None),
        ("rejected", "skipped-non-active", None),
        ("archived", "skipped-non-active", None),
    ],
)
def test_disposition_switch(status: str, label: str, successor_status: str | None) -> None:
    """The OQ-1 table, verbatim: only active/disabled get a successor."""
    assert ServiceAccountMigrationService._disposition(status) == (label, successor_status)


_SUCCESSORS = {"sva_now": "agnt_now", "sva_old": "agnt_old"}


def test_rule_parity_clean() -> None:
    counts = {
        ("sva_now", "cred_1"): 2,
        ("agnt_now", "cred_1"): 2,
        ("sva_old", "cred_2"): 3,
        ("agnt_old", "cred_2"): 1,  # edited since an earlier run: presence suffices
    }
    assert rule_parity_problems(counts, _SUCCESSORS, {"sva_now"}) == []


def test_rule_parity_exact_for_this_runs_migrations() -> None:
    counts = {("sva_now", "cred_1"): 2, ("agnt_now", "cred_1"): 1}
    [problem] = rule_parity_problems(counts, _SUCCESSORS, {"sva_now"})
    assert problem.service_account_id == "sva_now"
    assert "holds 1 inline permission rule(s)" in problem.reason
    assert "expected 2" in problem.reason


def test_rule_parity_never_blocks_on_an_earlier_successor_with_no_rules() -> None:
    """An earlier successor binding with no rules may have been emptied on
    purpose: a WARNING (never re-copied), not a refusal."""
    counts = {("sva_old", "cred_2"): 3}
    assert rule_parity_problems(counts, _SUCCESSORS, set()) == []
    [warning] = rule_parity_warnings(counts, _SUCCESSORS, set())
    assert (warning.service_account_id, warning.successor_agent_id) == ("sva_old", "agnt_old")
    assert warning.not_copied == "3 inline permission rule(s) for credential cred_2"
    assert warning.line().startswith("sva_old: 3 inline permission rule(s) for credential cred_2")
    assert "NOT copied to successor agent agnt_old" in warning.line()


def test_rule_parity_warnings_skip_this_runs_migrations_and_reported_pairs() -> None:
    counts = {("sva_now", "cred_1"): 2, ("sva_old", "cred_2"): 3}
    assert (
        rule_parity_warnings(counts, _SUCCESSORS, {"sva_now"}, skip={("sva_old", "cred_2")}) == []
    )


def test_rule_parity_ignores_actors_without_successor() -> None:
    """Skip-stamped / orphan sva_ ids and successor-side rows are not checked."""
    counts = {("sva_skipped", "cred_1"): 1, ("agnt_now", "cred_9"): 4}
    assert rule_parity_problems(counts, _SUCCESSORS, {"sva_now"}) == []


def test_retirement_error_names_every_service_account_once() -> None:
    error = ServiceAccountRetirementError(
        [
            RetirementProblem("sva_b", "not migrated"),
            RetirementProblem("sva_a", "migration failed (boom)"),
            RetirementProblem("sva_b", "successor agnt_b lacks the scope grant 'x'"),
        ]
    )
    assert error.service_account_ids == ("sva_a", "sva_b")
    message = str(error)
    assert message.startswith("Refusing to retire the service accounts (theme-8 Phase 4)")
    assert "2 service account(s) failed verification (sva_a, sva_b)" in message
    assert "Nothing was swept or dropped" in message
