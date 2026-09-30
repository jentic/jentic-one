"""The migration runner's theme-8 Phase-4 service-account retirement barrier.

Pins, against real SQLite databases driven through ``migrations.run.main``,
when the runner stops admin at ``d1e2f3a4b5c6`` to retire the service
accounts before the drop (``e2f3a4b5c6d7``): only on a full upgrade that has
not passed the drop yet, with control migrated first; a refusal exits
``EXIT_UPGRADE_STEP_FAILED`` and leaves admin before the drop. The retirement
itself is stubbed here (its end-to-end runs live in
``tests/integration/admin/test_phase4_drop_service_accounts.py``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.control.services.service_account_migration import (
    SAK_KEYS_RETIRED_WARNING,
    RetirementOutcome,
)
from jentic_one.migrations import run as run_mod

_PRE_DROP = run_mod.SA_DROP_PARENT_REVISION
_SA_TABLES = {"service_accounts", "service_account_credentials", "service_account_migration_acks"}


def _admin_revision(stack: Path) -> str:
    with sqlite3.connect(stack / "admin.db") as conn:
        return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _control_revision(stack: Path) -> str:
    with sqlite3.connect(stack / "control.db") as conn:
        return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _tables(stack: Path) -> set[str]:
    with sqlite3.connect(stack / "admin.db") as conn:
        return {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def _stub_retirement(
    monkeypatch: pytest.MonkeyPatch, stack: Path, *, code: int = 0
) -> list[tuple[str, str]]:
    """Replace the retirement; record (admin, control) revisions at call time."""
    calls: list[tuple[str, str]] = []

    def _retire() -> int:
        calls.append((_admin_revision(stack), _control_revision(stack)))
        return code

    monkeypatch.setattr(run_mod, "retire_service_accounts", _retire)
    return calls


def _below_the_drop(stack: Path) -> str:
    """Every DB at head except admin, left at the drop's parent (the drop is
    irreversible, so it is never walked back); returns control's head."""
    for name in run_mod._valid_dbs():
        if name != "admin":
            run_mod.upgrade(name)
    run_mod.upgrade("admin", _PRE_DROP)
    assert _tables(stack) >= _SA_TABLES
    return _control_revision(stack)


def test_fresh_install_is_not_pending_and_never_retires(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_retirement(monkeypatch, sqlite_stack)
    assert run_mod.sa_retirement_pending() is False  # uninitialized

    assert run_mod.main(["--skip-upgrade-steps"]) == 0

    assert calls == []
    assert not _SA_TABLES & _tables(sqlite_stack)
    assert run_mod.sa_retirement_pending() is False  # past the drop


def test_full_upgrade_retires_at_the_parent_revision_with_control_at_head(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control_head = _below_the_drop(sqlite_stack)
    assert run_mod.main(["--db", "admin", "--direction", "down", "--target", "-1"]) == 0
    assert run_mod.sa_retirement_pending() is True
    calls = _stub_retirement(monkeypatch, sqlite_stack)

    assert run_mod.main(["--skip-upgrade-steps"]) == 0

    assert calls == [(_PRE_DROP, control_head)]
    assert not _SA_TABLES & _tables(sqlite_stack)
    assert run_mod.sa_retirement_pending() is False


def test_refused_retirement_exits_4_and_nothing_is_dropped(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _below_the_drop(sqlite_stack)
    calls = _stub_retirement(monkeypatch, sqlite_stack, code=run_mod.EXIT_UPGRADE_STEP_FAILED)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED

    assert len(calls) == 1
    assert _admin_revision(sqlite_stack) == _PRE_DROP
    assert _tables(sqlite_stack) >= _SA_TABLES
    assert "==> admin complete" not in capsys.readouterr().out

    # After the fix, a re-run completes.
    _stub_retirement(monkeypatch, sqlite_stack)
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    assert not _SA_TABLES & _tables(sqlite_stack)


@pytest.mark.parametrize(
    "argv",
    [
        ["--db", "admin"],
        ["--target", "head"],
        ["--db", "admin", "--db", "control", "--db", "registry"],
    ],
    ids=["admin-only", "explicit-target", "admin-before-control"],
)
def test_partial_or_targeted_upgrade_skips_the_retirement(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """No retirement outside a full upgrade (control first) — the drop's own
    gate then decides (empty tables here, so it proceeds)."""
    _below_the_drop(sqlite_stack)
    calls = _stub_retirement(monkeypatch, sqlite_stack, code=run_mod.EXIT_UPGRADE_STEP_FAILED)

    assert run_mod.main([*argv, "--skip-upgrade-steps"]) == 0

    assert calls == []
    assert not _SA_TABLES & _tables(sqlite_stack)


def test_unexpected_retirement_error_exits_4_and_names_the_parent(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _below_the_drop(sqlite_stack)

    def broken_config() -> object:
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(run_mod, "load_config", broken_config)

    assert run_mod.retire_service_accounts() == run_mod.EXIT_UPGRADE_STEP_FAILED
    err = capsys.readouterr().err
    assert "the service-account retirement could not run (RuntimeError" in err
    assert f"admin stays at {_PRE_DROP}" in err


def test_sak_warning_lists_each_service_account_and_its_successor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = RetirementOutcome(
        action="retired", successors={"sva_a": "agnt_a", "sva_pending": None}
    )

    run_mod._print_sak_warning(outcome)

    assert capsys.readouterr().out.splitlines() == [
        f"==> WARNING (service-account retirement): {SAK_KEYS_RETIRED_WARNING}",
        "==> WARNING   sva_a -> agnt_a",
        "==> WARNING   sva_pending -> no successor agent (the account was not active or disabled)",
    ]


def test_sak_warning_is_silent_without_service_accounts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_mod._print_sak_warning(RetirementOutcome(action="no_tables"))

    assert capsys.readouterr().out == ""


def test_not_copied_warnings_are_printed_one_per_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = RetirementOutcome(action="retired", warnings=["sva_a: x", "sva_b: y"])

    run_mod._print_not_copied_warnings(outcome)

    assert capsys.readouterr().out.splitlines() == [
        "==> WARNING (service-account retirement, not copied): sva_a: x",
        "==> WARNING (service-account retirement, not copied): sva_b: y",
    ]
