"""Unit tests for the ``flatten-toolkits`` / ``export-toolkits`` CLI glue.

The jobs themselves are covered by the control integration suites
(``test_toolkit_flattening.py`` / ``test_toolkit_export.py``); this file pins
the CLI-specific seam — flag validation (``--acknowledge`` requires
``--verify``; ``--diff-only`` and ``--verify`` are exclusive; export needs
exactly one of ``--out``/``--import``) and the JSONL report writer — with no
database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jentic_one import __main__ as cli
from jentic_one.control.services.toolkit_flattening import Finding


def _expect_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    assert excinfo.value.code == 2


def test_acknowledge_without_verify_is_a_usage_error() -> None:
    """The sentinel records a *passed verification* — bare --acknowledge is refused."""
    _expect_usage_error(["flatten-toolkits", "--acknowledge"])


def test_diff_only_and_verify_are_mutually_exclusive() -> None:
    _expect_usage_error(["flatten-toolkits", "--diff-only", "--verify"])


def test_export_requires_exactly_one_of_out_and_import(tmp_path: Path) -> None:
    _expect_usage_error(["export-toolkits"])
    _expect_usage_error(
        ["export-toolkits", "--out", str(tmp_path / "a.json"), "--import", str(tmp_path / "a.json")]
    )


def test_write_report_emits_one_json_line_per_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    findings = [
        Finding("rule_conflict", {"agent_id": "agnt_1", "credential_id": "cred_1"}),
        Finding("active_toolkit_key", {"key_id": "ck_1"}),
    ]

    report_path = tmp_path / "report.jsonl"
    cli._write_report(findings, str(report_path))
    lines = [json.loads(line) for line in report_path.read_text().splitlines()]
    assert lines == [
        {"category": "rule_conflict", "agent_id": "agnt_1", "credential_id": "cred_1"},
        {"category": "active_toolkit_key", "key_id": "ck_1"},
    ]

    cli._write_report(findings, None)
    stdout_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert stdout_lines == lines
