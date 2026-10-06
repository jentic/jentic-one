"""Tests for the migration runner's post-migration upgrade steps.

Pins, against real SQLite databases driven through the CLI entrypoint
(``migrations.run.main``), the runner contract: steps run only after a full
upgrade to head, a failing step exits ``EXIT_UPGRADE_STEP_FAILED`` without
being ledgered, and an operator skip defers a step. Most tests replace the
registered steps with a fake one; the index-repair tests for
``5c7e2a9d4f16`` also live here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.control.services import upgrade_steps as steps_mod
from jentic_one.control.services.upgrade_steps import UpgradeStepOutcome, UpgradeStepSpec
from jentic_one.migrations import run as run_mod
from jentic_one.shared.context import Context

_STEP = "test_step_runner"


def _query(db: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def _execute(db: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(sql, params)
        conn.commit()


def _ledger(stack: Path) -> list[str]:
    return [str(row[0]) for row in _query(stack / "control.db", "SELECT name FROM upgrade_steps")]


_BINDING_INDEXES = {
    "ix_agent_credential_bindings_agent_id",
    "ix_agent_credential_bindings_credential_id",
    "ix_agent_credential_bindings_rule_set_id",
    "ix_agent_credential_bindings_created_at",
    "ix_agent_credential_bindings_created_by",
}


def _binding_indexes(stack: Path) -> set[str]:
    return {
        str(row[0])
        for row in _query(
            stack / "admin.db",
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            " AND tbl_name = 'agent_credential_bindings'",
        )
    }


def test_binding_table_indexes_survive_the_sqlite_rebuild(sqlite_stack: Path) -> None:
    """Pins the SQLite batch rebuild in b9d0e1f2a3b4: it must keep every named index."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    assert _binding_indexes(sqlite_stack) == _BINDING_INDEXES


def test_repair_migration_restores_indexes_a_prior_rebuild_dropped(sqlite_stack: Path) -> None:
    """A dev database that ran the original b9d0e1f2a3b4 regains its indexes."""
    # Admin only up to the rebuild (never walked back: the SA drop above it is
    # irreversible); everything else at head.
    for name in run_mod._valid_dbs():
        if name != "admin":
            run_mod.upgrade(name)
    run_mod.upgrade("admin", "b9d0e1f2a3b4")  # pragma: allowlist secret
    for name in _BINDING_INDEXES:
        _execute(sqlite_stack / "admin.db", f"DROP INDEX {name}")

    assert run_mod.main(["--db", "admin"]) == 0

    assert _binding_indexes(sqlite_stack) == _BINDING_INDEXES


def _register(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[int]:
    """Register one fake step for the runner; returns its call log."""
    calls: list[int] = []

    async def _run(_ctx: Context) -> UpgradeStepOutcome:
        calls.append(1)
        if fail:
            raise RuntimeError("simulated step failure")
        return UpgradeStepOutcome(name=_STEP, action="performed")

    monkeypatch.setattr(steps_mod, "STEPS", (UpgradeStepSpec(name=_STEP, run=_run),))
    return calls


def test_no_registered_steps_is_a_clean_no_op(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no step registered a full upgrade runs none and never touches the ledger."""
    monkeypatch.setattr(steps_mod, "STEPS", ())
    assert run_mod.main([]) == 0
    assert "upgrade step" not in capsys.readouterr().out
    assert _ledger(sqlite_stack) == []


def test_full_upgrade_runs_the_rule_set_curation_step_every_time(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The curation step is repeatable: every full upgrade runs it; one ledger row."""
    assert run_mod.main([]) == 0
    assert f"upgrade step {steps_mod.RULE_SETS_MARK_CURATED}: performed" in capsys.readouterr().out
    assert run_mod.main([]) == 0
    assert f"upgrade step {steps_mod.RULE_SETS_MARK_CURATED}: performed" in capsys.readouterr().out
    assert _ledger(sqlite_stack) == [steps_mod.RULE_SETS_MARK_CURATED]


# Control and admin heads of the 0.41.0 release (the registry head is unchanged).
_CONTROL_041_HEAD = "f3c4d5e6a7b8"  # pragma: allowlist secret
_ADMIN_041_HEAD = "e2f3a4b5c6d7"  # pragma: allowlist secret

_ADMIN_USER = "usr_up_admin"
_ALICE = "usr_up_alice"
_BOB = "usr_up_bob"


def _seed_041_data(stack: Path) -> dict[str, str]:
    """Users, an org:admin grant, rule sets and bindings as a 0.41 install holds them.

    Returns the rule set ids by label. Bob's agent is attached to Alice's set
    (cross-owner) and to the admin's set; Alice's agent to her own set.
    """
    admin_db, control_db = stack / "admin.db", stack / "control.db"
    for uid in (_ADMIN_USER, _ALICE, _BOB):
        _execute(
            admin_db,
            "INSERT INTO users (id, email, first_name, last_name) VALUES (?, ?, 'U', 'P')",
            (uid, f"{uid}@test.local"),
        )
    _execute(
        admin_db,
        "INSERT INTO user_permission_grants (id, user_id, permission) "
        "VALUES ('perm_up_admin', ?, 'org:admin')",
        (_ADMIN_USER,),
    )
    sets = {"admin": ("prs_up_admin", _ADMIN_USER), "alice": ("prs_up_alice", _ALICE)}
    for label, (set_id, creator) in sets.items():
        _execute(
            control_db,
            "INSERT INTO permission_rule_sets (id, name, created_by) VALUES (?, ?, ?)",
            (set_id, f"up-{label}", creator),
        )
    for agent_id, owner in (("agnt_up_alice", _ALICE), ("agnt_up_bob", _BOB)):
        _execute(
            admin_db,
            "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
            "VALUES (?, ?, ?, ?, 'approved', ?)",
            (agent_id, f"{agent_id}-name", owner, owner, owner),
        )
    for binding_id, agent_id, credential_id, set_id in (
        ("acb_up_alice_own", "agnt_up_alice", "cred_up_a", "prs_up_alice"),
        ("acb_up_bob_cross", "agnt_up_bob", "cred_up_b", "prs_up_alice"),
        ("acb_up_bob_admin", "agnt_up_bob", "cred_up_c", "prs_up_admin"),
    ):
        _execute(
            admin_db,
            "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, rule_set_id) "
            "VALUES (?, ?, ?, ?)",
            (binding_id, agent_id, credential_id, set_id),
        )
    return {label: set_id for label, (set_id, _) in sets.items()}


def _migrate_to_041_heads() -> None:
    run_mod.upgrade("registry")
    run_mod.upgrade("control", _CONTROL_041_HEAD)
    run_mod.upgrade("admin", _ADMIN_041_HEAD)


def _curated(stack: Path) -> dict[str, bool]:
    rows = _query(stack / "control.db", "SELECT id, curated FROM permission_rule_sets")
    return {str(set_id): bool(curated) for set_id, curated in rows}


def _step_warnings(err: str) -> list[str]:
    prefix = f"==> WARNING ({steps_mod.RULE_SETS_MARK_CURATED}): "
    return [line[len(prefix) :] for line in err.splitlines() if line.startswith(prefix)]


def test_upgrade_from_041_curates_admin_sets_and_warns_on_cross_owner_bindings(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """From the 0.41.0 heads: admin sets marked, only the cross-owner binding listed."""
    _migrate_to_041_heads()
    ids = _seed_041_data(sqlite_stack)
    capsys.readouterr()

    assert run_mod.main([]) == 0

    assert _curated(sqlite_stack) == {ids["admin"]: True, ids["alice"]: False}
    captured = capsys.readouterr()
    assert f"upgrade step {steps_mod.RULE_SETS_MARK_CURATED}: performed" in captured.out
    assert '"cross_owner_bindings": 1' in captured.out
    header, *lines = _step_warnings(captured.err)
    assert header.startswith("1 agent credential binding(s) use a non-curated shared rule set")
    assert "UPDATE permission_rule_sets SET curated = true" in header
    assert lines == [
        "binding acb_up_bob_cross: agent agnt_up_bob ('agnt_up_bob-name') owned by "
        f"{_BOB}, credential cred_up_b, rule set {ids['alice']} ('up-alice') created by {_ALICE}"
    ]


def test_curation_rerun_marks_a_set_created_after_the_first_run(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A set an older release created on the newer schema is curated by the next upgrade."""
    _migrate_to_041_heads()
    _seed_041_data(sqlite_stack)
    assert run_mod.main([]) == 0
    # An older release redeployed on this schema writes no ``curated`` value.
    _execute(
        sqlite_stack / "control.db",
        "INSERT INTO permission_rule_sets (id, name, created_by) VALUES (?, ?, ?)",
        ("prs_up_later", "up-later", _ADMIN_USER),
    )
    capsys.readouterr()

    assert run_mod.main([]) == 0

    assert _curated(sqlite_stack)["prs_up_later"] is True
    out = capsys.readouterr().out
    assert f"upgrade step {steps_mod.RULE_SETS_MARK_CURATED}: performed" in out
    assert '"marked": 1' in out
    assert _ledger(sqlite_stack) == [steps_mod.RULE_SETS_MARK_CURATED]


def test_skipping_the_curation_step_leaves_it_pending(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrate_to_041_heads()
    ids = _seed_041_data(sqlite_stack)

    assert run_mod.main(["--skip-upgrade-step", steps_mod.RULE_SETS_MARK_CURATED]) == 0
    assert _curated(sqlite_stack)[ids["admin"]] is False
    assert _ledger(sqlite_stack) == []
    capsys.readouterr()
    assert run_mod.main(["--check"]) == run_mod.CHECK_EXIT_NEEDS_MIGRATION
    assert f"STATUS upgrade-step:{steps_mod.RULE_SETS_MARK_CURATED} pending" in (
        capsys.readouterr().out
    )

    assert run_mod.main([]) == 0
    assert _curated(sqlite_stack)[ids["admin"]] is True


@pytest.mark.parametrize(
    "runs",
    [[["--skip-upgrade-steps"]], [["--db", "admin"], ["--db", "control"]]],
    ids=["steps-skipped", "one-database-at-a-time"],
)
def test_check_reports_a_pending_step_after_a_roll_forward_without_it(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], runs: list[list[str]]
) -> None:
    """Schemas at head, step never run: --check says pending and exits 3."""
    _migrate_to_041_heads()
    for argv in runs:
        assert run_mod.main(argv) == 0
    capsys.readouterr()

    assert run_mod.main(["--check"]) == run_mod.CHECK_EXIT_NEEDS_MIGRATION
    out = capsys.readouterr().out
    for name in ("admin", "control", "registry"):
        assert f"STATUS {name} current" in out
    assert f"STATUS upgrade-step:{steps_mod.RULE_SETS_MARK_CURATED} pending" in out
    assert out.splitlines()[-1] == "OVERALL pending"

    # A subset that leaves out a database the steps touch judges the schema only.
    assert run_mod.main(["--check", "--db", "control"]) == 0
    assert "upgrade-step" not in capsys.readouterr().out

    assert run_mod.main([]) == 0
    capsys.readouterr()
    assert run_mod.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "upgrade-step" not in out
    assert "OVERALL current" in out


def test_check_on_a_schema_behind_head_lists_no_steps(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only a current schema is probed for steps; the verdict stays the schema's."""
    _migrate_to_041_heads()
    capsys.readouterr()

    assert run_mod.main(["--check"]) == run_mod.CHECK_EXIT_NEEDS_MIGRATION
    out = capsys.readouterr().out
    assert "upgrade-step" not in out
    assert "OVERALL pending" in out


def test_fresh_check_stays_uninitialized(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No schema at all is still ``uninitialized`` (migrated unattended), not pending."""
    assert run_mod.main(["--check"]) == run_mod.CHECK_EXIT_NEEDS_MIGRATION
    out = capsys.readouterr().out
    assert "upgrade-step" not in out
    assert "OVERALL uninitialized" in out


def test_full_upgrade_runs_a_registered_step_exactly_once(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _register(monkeypatch)

    assert run_mod.main([]) == 0
    assert f"upgrade step {_STEP}: performed" in capsys.readouterr().out
    assert run_mod.main([]) == 0
    assert f"upgrade step {_STEP}: already_done" in capsys.readouterr().out
    assert len(calls) == 1
    assert _ledger(sqlite_stack) == [_STEP]


@pytest.mark.parametrize(
    "argv",
    [["--skip-upgrade-steps"], ["--db", "admin"], ["--db", "control"], ["--target", "heads"]],
    ids=["skip-flag", "admin-only", "control-only", "pinned-target"],
)
def test_steps_do_not_run_without_a_full_upgrade(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    calls = _register(monkeypatch)

    assert run_mod.main(argv) == 0

    assert calls == []
    assert _ledger(sqlite_stack) == []


def test_failing_step_exits_with_the_upgrade_step_code(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed step stops the upgrade and is not ledgered, so the next run retries it."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _register(monkeypatch, fail=True)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    captured = capsys.readouterr()
    assert f"upgrade step {_STEP}: failed" in captured.out
    assert "left work undone" in captured.err
    assert _ledger(sqlite_stack) == []


def test_unexpected_error_exits_with_the_upgrade_step_code(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema at head, steps unrunnable: exit 4 ("steps undone"), not a traceback."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _register(monkeypatch)

    def broken_config() -> None:
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(run_mod, "load_config", broken_config)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    assert "upgrade steps could not run" in capsys.readouterr().err


def test_skip_one_step_defers_it(sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    calls = _register(monkeypatch)

    assert run_mod.main(["--skip-upgrade-step", _STEP]) == 0
    assert calls == []
    assert _ledger(sqlite_stack) == [], "a skipped step runs on the next full upgrade"

    assert run_mod.main([]) == 0
    assert len(calls) == 1


def test_unknown_step_name_is_rejected(sqlite_stack: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run_mod.main(["--skip-upgrade-step", "no_such_step"])
    assert exc.value.code == 2
