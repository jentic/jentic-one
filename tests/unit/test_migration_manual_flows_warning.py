"""The upgrade warns once that agents may now ask for any registry API's credential.

``control.connect.manual_flows_enabled`` is on by default, upgrades included,
so an operator upgrading a control DB across the revision that adds API-target
connect sessions hears about it from the migration runner. A fresh install,
a re-run at head, and a deployment that turned the setting off stay quiet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jentic_one.migrations.run import (
    MANUAL_FLOWS_REVISION,
    MANUAL_FLOWS_UPGRADE_WARNING,
    main,
    upgrade,
)

_PARENT_REV = "aa1b2c3d4e5f"  # pragma: allowlist secret


def _warned(out: str) -> bool:
    return MANUAL_FLOWS_UPGRADE_WARNING in out


def test_upgrade_across_the_revision_warns(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    upgrade("control", _PARENT_REV)
    capsys.readouterr()
    assert main(["--db", "control"]) == 0
    out = capsys.readouterr().out
    assert _warned(out)
    assert "==> WARNING (connect sessions):" in out


def test_upgrade_with_the_setting_off_stays_quiet(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENTIC__CONTROL__CONNECT__MANUAL_FLOWS_ENABLED", "false")
    upgrade("control", _PARENT_REV)
    capsys.readouterr()
    assert main(["--db", "control"]) == 0
    assert not _warned(capsys.readouterr().out)


def test_fresh_install_and_rerun_at_head_stay_quiet(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--db", "control"]) == 0
    assert not _warned(capsys.readouterr().out)
    assert main(["--db", "control"]) == 0
    assert not _warned(capsys.readouterr().out)


def test_db_already_past_the_revision_stays_quiet(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    upgrade("control", MANUAL_FLOWS_REVISION)
    capsys.readouterr()
    assert main(["--db", "control"]) == 0
    assert not _warned(capsys.readouterr().out)
