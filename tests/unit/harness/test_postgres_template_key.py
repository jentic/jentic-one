"""The Postgres test harness's naming and keys (tests/harness/postgres.py): the
template key changes with everything that decides the migrated database, the
migrations get exactly the tests' configuration, and every name parser keeps
test databases, templates and other databases apart."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from tests.harness import fixture_lock, postgres


def _tree(root: Path) -> Path:
    migrations = root / "src" / "jentic_one" / "migrations"
    (migrations / "admin" / "versions").mkdir(parents=True)
    (migrations / "env.py").write_text("from jentic_one.shared.db import types\n")
    (migrations / "admin" / "versions" / "0001_init.py").write_text("import jentic_one.a\n")
    # A module reached only through another: a server default's enum.
    (root / "src" / "jentic_one" / "a.py").write_text("from jentic_one.shared.models import b\n")
    models = root / "src" / "jentic_one" / "shared" / "models"
    models.mkdir(parents=True)
    (models / "b.py").write_text("STATUS = 'active'\n")
    (root / "alembic.ini").write_text("[admin]\n")
    (root / "uv.lock").write_text("version = 1\n")
    (root / "docker" / "local-setup").mkdir(parents=True)
    (root / "docker" / "local-setup" / "init-schemas.sql").write_text("CREATE ROLE x;\n")
    (root / "config").mkdir()
    (root / "config" / "local.yaml").write_text(
        "databases:\n  admin:\n    name: jentic\n    schema_name: admin\nruntime:\n  debug: true\n"
    )
    (root / "README.md").write_text("docs\n")
    return root


@pytest.mark.parametrize(
    "changed",
    [
        "src/jentic_one/migrations/admin/versions/0001_init.py",
        "src/jentic_one/migrations/env.py",
        "src/jentic_one/a.py",
        "src/jentic_one/shared/models/b.py",
        "alembic.ini",
        "uv.lock",
        "docker/local-setup/init-schemas.sql",
    ],
)
def test_the_key_changes_with_every_input(tmp_path: Path, changed: str) -> None:
    root = _tree(tmp_path)
    before = postgres.template_key(root)
    with (root / changed).open("a") as file:
        file.write("# changed\n")
    assert postgres.template_key(root) != before


def test_a_new_module_changes_the_key(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = postgres.template_key(root)
    (root / "src/jentic_one/migrations/admin/versions/0002_more.py").write_text("X = 2\n")
    assert postgres.template_key(root) != before


def test_the_effective_migration_config_changes_the_key(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = postgres.template_key(root)
    (root / "config" / "local.yaml").write_text(
        "databases:\n  admin:\n    name: jentic\n    schema_name: admin\nruntime:\n  debug: false\n"
    )
    assert postgres.template_key(root) != before


def test_what_the_tests_override_does_not_change_the_key(tmp_path: Path) -> None:
    # The databases section is replaced by the tests' own, so local.yaml's
    # database name or a comment cannot split the cache.
    root = _tree(tmp_path)
    before = postgres.template_key(root)
    (root / "config" / "local.yaml").write_text(
        "# a comment\ndatabases:\n  admin:\n    name: other\n    schema_name: elsewhere\n"
        "runtime:\n  debug: true\n"
    )
    assert postgres.template_key(root) == before


def test_docs_do_not_change_the_key(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = postgres.template_key(root)
    (root / "README.md").write_text("other docs\n")
    assert postgres.template_key(root) == before


def test_the_key_ignores_the_run_and_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    before = postgres.template_key()
    monkeypatch.setenv("JENTIC_PG_PORT", "55999")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    monkeypatch.setenv("JENTIC_PG_CHECKOUT", "other1")
    assert postgres.template_key() == before


def test_the_key_has_a_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path)
    before = postgres.template_key(root)
    monkeypatch.setattr(postgres, "KEY_VERSION", postgres.KEY_VERSION + 1)
    assert postgres.template_key(root) != before


def test_the_migrations_and_the_tests_use_one_configuration() -> None:
    config = postgres.migration_config("jentic_tpl_0123456789ab", postgres.pg_port())
    tests = postgres.postgres_config("jentic_tpl_0123456789ab")
    for surface in postgres.DB_NAMES:
        mine = config["databases"][surface]
        theirs = getattr(tests.databases, surface)
        assert mine["backend"] == theirs.backend == "postgres"
        assert mine["host"] == theirs.host
        assert mine["port"] == theirs.port
        assert mine["name"] == theirs.name
        assert mine["user"] == theirs.user
        assert mine["password"] == theirs.password.get_secret_value()
        assert mine["schema_name"] == theirs.schema_name
    assert config["credentials"]["encryption"] == postgres.encryption_config()["encryption"]
    # The rest of local.yaml, as `make start-fixtures` migrates with it.
    local = yaml.safe_load((postgres.REPO_ROOT / "config" / "local.yaml").read_text())
    assert config["credentials"]["providers"] == local["credentials"]["providers"]


def test_no_inherited_override_reaches_the_migrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JENTIC__DATABASES__REGISTRY__SCHEMA_NAME", "custom")
    monkeypatch.setenv("JENTIC_CONFIG_FILE", "custom.yaml")
    env = postgres.migration_env(tmp_path / "config.yaml")
    assert env["JENTIC_CONFIG_FILE"] == str(tmp_path / "config.yaml")
    assert not [name for name in env if name.startswith("JENTIC") and name != "JENTIC_CONFIG_FILE"]
    assert "PATH" in env


def test_the_init_sql_creates_the_shared_roles() -> None:
    sql = (postgres.REPO_ROOT / postgres.INIT_SQL).read_text()
    for role, password in postgres.ROLES.items():
        assert re.search(rf"CREATE ROLE {role} LOGIN PASSWORD '{password}'", sql), role


def test_database_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JENTIC_PG_CHECKOUT", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    checkout = postgres.checkout_id()
    assert len(checkout) == 8
    name = postgres.pg_database()
    match = postgres.TEST_DATABASE.fullmatch(name)
    assert match is not None and match[1] == checkout and match[3] == "main"
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw15")
    worker_name = postgres.pg_database()
    assert worker_name.endswith("_gw15")
    # One run id per process, in every worker label.
    assert worker_name.split("_")[3] == name.split("_")[3]
    # Well under Postgres's 63-byte limit, so names are never truncated.
    monkeypatch.setenv("JENTIC_PG_CHECKOUT", "abcdefghijkl")
    assert len(postgres.pg_database()) < 63
    assert postgres.template_name("ab" * 32) == "jentic_tpl_abababababab"


def test_two_processes_never_share_a_name() -> None:
    script = "from tests.harness import postgres; print(postgres.pg_database())"
    env = {**os.environ, "JENTIC_PG_CHECKOUT": "same", "PYTEST_XDIST_WORKER": "gw0"}
    names = {
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=postgres.REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(names) == 2
    assert all(postgres.TEST_DATABASE.fullmatch(name) for name in names)


@pytest.mark.parametrize("value", ["Not Valid", "UPPER", "a" * 13])
def test_invalid_checkout_ids(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("JENTIC_PG_CHECKOUT", value)
    with pytest.raises(ValueError, match="JENTIC_PG_CHECKOUT"):
        postgres.checkout_id()


@pytest.mark.parametrize(
    ("name", "checkout", "every", "selected"),
    [
        ("jentic_test_abc_0123456789ab_gw0", "abc", False, True),
        ("jentic_test_other_0123456789ab_gw0", "abc", False, False),
        ("jentic_test_other_0123456789ab_gw0", "abc", True, True),
        ("jentic_tpl_0123456789ab", "abc", False, False),
        ("jentic_tpl_0123456789ab", "tpl", False, False),
        ("jentic_tpl_0123456789ab", "abc", True, True),
        ("jentic_gw0", "abc", True, True),
        ("jentic_gw12", "abc", True, True),
        ("jentic_gw0", "abc", False, False),
        ("jentic", "abc", True, False),
        ("jentic_enterprise_dev", "abc", True, False),
        ("postgres", "abc", True, False),
    ],
)
def test_what_cleaning_selects(name: str, checkout: str, every: bool, selected: bool) -> None:
    got = postgres._selected_for_clean(name, checkout=checkout, every_checkout=every)
    assert got is selected


def test_a_template_is_never_a_test_database() -> None:
    assert not postgres.TEST_DATABASE.fullmatch("jentic_tpl_0123456789ab")
    assert not postgres.TEMPLATE_DATABASE.fullmatch("jentic_test_abc_0123456789ab_gw0")


def test_reclaimable() -> None:
    now = time.time()
    alive = f"jentic test database {int(now)} {os.getpid()}"
    dead = f"jentic test database {int(now)} 999999999"
    assert not postgres.reclaimable(alive, now=now)
    assert postgres.owned_by_a_live_run(alive, now=now)
    # A pid this host cannot have: the run is over.
    assert postgres.reclaimable(dead, now=now)
    assert not postgres.owned_by_a_live_run(dead, now=now)
    # A live pid on a stamp older than the limit: stale.
    assert postgres.reclaimable(alive, now=now + postgres.STALE_DATABASE_S + 1)
    # Kept, unstamped and template databases are no run's, and never reclaimed.
    for comment in (f"jentic test kept {int(now)}", None, f"jentic test template {int(now)}"):
        assert not postgres.reclaimable(comment, now=now)
        assert not postgres.owned_by_a_live_run(comment, now=now)


def test_the_fixture_lock_is_one_per_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", "/cache")
    monkeypatch.setenv("JENTIC_PG_PORT", "5432")
    assert fixture_lock.lock_path() == Path("/cache/jentic/fixtures-localhost-5432.lock")
    assert fixture_lock.lock_path(55433) != fixture_lock.lock_path(5432)
