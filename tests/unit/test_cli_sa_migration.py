"""Unit tests for the ``migrate-service-accounts`` CLI glue.

The job itself is covered by ``tests/integration/control/
test_service_account_migration.py`` against real databases; this file pins
the CLI seam with a fake service and no database: ``--sweep-migrated``
honours ``--report``, the acknowledge-refusal hint names the right next
step, and the acknowledgement's finding count excludes the verify summary.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one import __main__ as cli
from jentic_one.control.services.service_account_migration import (
    SweepOutcome,
    VerificationResult,
)


class _FakeContext:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...

    async def __aenter__(self) -> _FakeContext:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@contextmanager
def _patched_service(svc: MagicMock) -> Iterator[None]:
    with (
        patch.object(cli, "load_config", return_value=MagicMock()),
        patch.object(cli, "configure_logging"),
        patch.object(cli, "Context", _FakeContext),
        patch.object(cli, "ServiceAccountMigrationService", return_value=svc),
    ):
        yield


def _verify_result(**counts: int) -> VerificationResult:
    c = {
        "unstamped_count": 0,
        "grant_twin_missing_count": 0,
        "unrevoked_token_count": 0,
        "digest_mismatch_count": 0,
        "post_stamp_mutation_count": 0,
        "inline_rule_mismatch_count": 0,
        **counts,
    }
    return VerificationResult(
        passed=not any(c.values()),
        unstamped_count=c["unstamped_count"],
        grant_twin_missing_count=c["grant_twin_missing_count"],
        unrevoked_token_count=c["unrevoked_token_count"],
        digest_mismatch_count=c["digest_mismatch_count"],
        post_stamp_mutation_count=c["post_stamp_mutation_count"],
        inline_rule_mismatch_count=c["inline_rule_mismatch_count"],
    )


def test_sweep_migrated_writes_the_report(tmp_path: Path) -> None:
    sweep = SweepOutcome(
        swept=["sva_1"],
        access_tokens_revoked=1,
        refresh_tokens_revoked=2,
        permission_rules_deleted=3,
        rows=[{"category": "sweep_row", "service_account_id": "sva_1"}],
    )
    svc = MagicMock()
    svc.sweep = AsyncMock(return_value=sweep)
    report = tmp_path / "sweep.jsonl"

    with _patched_service(svc):
        rc = cli.main(["migrate-service-accounts", "--sweep-migrated", "--report", str(report)])

    assert rc == 0
    svc.sweep.assert_awaited_once_with(ignore_age_gate=True)
    lines = [json.loads(line) for line in report.read_text().splitlines()]
    assert lines[0] == {"category": "sweep_row", "service_account_id": "sva_1"}
    summary = lines[-1]
    assert summary["category"] == "sweep_summary"
    assert (summary["swept"], summary["permission_rules_deleted"]) == (1, 3)
    assert summary["ignore_age_gate"] is True


@pytest.mark.parametrize(
    "counts",
    [
        {"unrevoked_token_count": 2},
        {"inline_rule_mismatch_count": 1},
        {"unrevoked_token_count": 2, "inline_rule_mismatch_count": 1},
    ],
)
def test_refusal_points_at_sweep_when_only_sweep_can_heal(counts: dict[str, int]) -> None:
    message = cli._sa_acknowledge_message(_verify_result(**counts))
    assert "REFUSED" in message
    assert "--sweep-migrated" in message


@pytest.mark.parametrize(
    "counts",
    [
        {"unstamped_count": 1},
        {"grant_twin_missing_count": 1},
        {"digest_mismatch_count": 1},
        {"post_stamp_mutation_count": 1},
        {"unstamped_count": 1, "unrevoked_token_count": 1},
    ],
)
def test_refusal_points_at_migration_otherwise(counts: dict[str, int]) -> None:
    message = cli._sa_acknowledge_message(_verify_result(**counts))
    assert "REFUSED" in message
    assert "--sweep-migrated" not in message
    assert "run migrate-service-accounts first" in message


def test_acknowledged_message() -> None:
    result = _verify_result()
    result.acknowledged = True
    assert "acknowledgement recorded" in cli._sa_acknowledge_message(result)


def test_finding_count_excludes_the_verify_summary() -> None:
    result = _verify_result()
    result.findings.append({"category": "verify_summary", "passed": True})
    assert result.finding_count == 0
    result.findings.append({"category": "some_finding"})
    assert result.finding_count == 1


def test_finding_count_excludes_informational_review_lines() -> None:
    """Admin-level carry-over lines are reported, but a passing verify still
    records 0 findings on the acknowledgement row."""
    result = _verify_result()
    result.findings.append({"category": "successor_admin_scope", "informational": True})
    assert result.finding_count == 0


def test_verify_admin_scope_findings_are_informational(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Admin-level grants carried onto successors print a REVIEW line but
    never turn a passing verify into a failing exit code."""
    result = _verify_result()
    result.successor_admin_scope_count = 2
    svc = MagicMock()
    svc.verify = AsyncMock(return_value=result)

    with _patched_service(svc):
        rc = cli.main(["migrate-service-accounts", "--verify"])

    assert rc == 0
    err = capsys.readouterr().err
    assert "verify PASSED" in err
    assert "REVIEW (informational, does not fail verify): 2 admin-level grant(s)" in err


def test_digest_mismatch_refusal_points_at_the_report_lines() -> None:
    """#1416: a genuine digest mismatch names where the failing ids are."""
    message = cli._sa_acknowledge_message(_verify_result(digest_mismatch_count=2))
    assert "REFUSED" in message
    assert "2 digest mismatch(es) need operator repair" in message
    assert "digest_mismatch report lines" in message


def test_superseded_digests_are_informational(capsys: pytest.CaptureFixture[str]) -> None:
    """#1416: successors rotated/archived after migration print a NOTE with the
    sweep hint but never fail the verify."""
    result = _verify_result()
    result.superseded_digest_count = 1
    result.findings.append({"category": "successor_digest_superseded", "informational": True})
    svc = MagicMock()
    svc.verify = AsyncMock(return_value=result)

    with _patched_service(svc):
        rc = cli.main(["migrate-service-accounts", "--verify"])

    assert rc == 0
    assert result.finding_count == 0
    err = capsys.readouterr().err
    assert "verify PASSED" in err
    assert "NOTE (informational, does not fail verify): 1 successor agent(s)" in err
    assert "--sweep-migrated" in err
