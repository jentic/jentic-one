"""The Postgres test harness's own databases (tests/harness/postgres.py), on a
real server with real processes: runs never share or drop each other's
databases, templates carry their own init SQL, cleaning, pruning and destroying
touch only what they should, and the shared roles are checked.

Each case uses a checkout id of its own, so it never touches a concurrent run's
databases.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.db.session import get_database_url
from tests.harness import fixture_lock, postgres

# Every test here is about the Postgres fixture server itself (its databases,
# templates, roles and lock), so none applies to the SQLite backend.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite",
        reason="the Postgres test harness needs the Postgres fixture server",
    ),
]


async def _sql(statement: str, database: str = "postgres") -> list[tuple[object, ...]]:
    config = postgres._maintenance_config(database)
    engine = create_async_engine(get_database_url(config), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(statement))
            return [tuple(row) for row in result] if result.returns_rows else []
    finally:
        await engine.dispose()


def _create(name: str, *, stamp: str | None = None) -> None:
    asyncio.run(_sql(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    if stamp is not None:
        asyncio.run(_sql(f"COMMENT ON DATABASE \"{name}\" IS '{stamp}'"))


def _exists(name: str) -> bool:
    rows = asyncio.run(_sql(f"SELECT 1 FROM pg_database WHERE datname = '{name}'"))
    return bool(rows)


def _drop(*names: str) -> None:
    for name in names:
        asyncio.run(_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _test_name(checkout: str, worker: str = "gw0") -> str:
    return f"{postgres.TEST_PREFIX}{checkout}_{uuid.uuid4().hex[:12]}_{worker}"


def _live_stamp() -> str:
    return f"jentic test database {int(time.time())} {os.getpid()}"


def test_the_template_takes_no_connections() -> None:
    template = postgres.template_name(postgres.template_key())
    rows = asyncio.run(
        _sql(f"SELECT datistemplate, datallowconn FROM pg_database WHERE datname = '{template}'")
    )
    assert rows == [(True, False)]


def test_this_runs_database_carries_the_init_sql_and_head() -> None:
    database = postgres.pg_database()
    grants = asyncio.run(
        _sql(
            "SELECT has_schema_privilege('registry_user', 'registry', 'CREATE'), "
            "has_schema_privilege('control_user', 'control', 'CREATE'), "
            "has_schema_privilege('admin_user', 'admin', 'CREATE')",
            database,
        )
    )
    assert grants == [(True, True, True)]
    versions = asyncio.run(_sql("SELECT count(*) FROM registry.alembic_version", database))
    assert versions == [(1,)]


def test_each_template_carries_its_own_init_sql(tmp_path: Path) -> None:
    # Two checkouts with different database-local init SQL: each database gets
    # exactly its own, whatever the other applied.
    first, second = (f"jentic_tpl_{uuid.uuid4().hex[:12]}" for _ in range(2))
    sql = (postgres.REPO_ROOT / postgres.INIT_SQL).read_text()
    roots = {}
    for name, extra in ((first, "first_only"), (second, "second_only")):
        root = tmp_path / extra
        (root / postgres.INIT_SQL).parent.mkdir(parents=True)
        (root / postgres.INIT_SQL).write_text(sql + f"\nCREATE SCHEMA {extra};\n")
        roots[name] = root
    try:
        for name, root in roots.items():
            _create(name)
            asyncio.run(postgres.apply_init_sql(name, root))
        for name, own, other in (
            (first, "first_only", "second_only"),
            (second, "second_only", "first_only"),
        ):
            schemas = {
                row[0] for row in asyncio.run(_sql("SELECT nspname FROM pg_namespace", name))
            }
            assert {"registry", "control", "admin", own} <= schemas
            assert other not in schemas
    finally:
        _drop(first, second)


_RUN_SCRIPT = textwrap.dedent(
    """
    import asyncio, pathlib, sys, time
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from jentic_one.shared.db.session import get_database_url
    from tests.harness import postgres

    me, other, scratch = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])

    def wait(marker):
        deadline = time.time() + 120
        while not (scratch / marker).exists():
            if time.time() > deadline:
                sys.exit(f"timed out waiting for {marker}")
            time.sleep(0.05)

    async def use():
        config = postgres.postgres_config().databases.registry
        engine = create_async_engine(get_database_url(config))
        try:
            async with engine.connect() as conn:
                query = text("SELECT count(*) FROM registry.alembic_version")
                return (await conn.execute(query)).scalar()
        finally:
            await engine.dispose()

    (scratch / f"{me}.started").touch()
    wait(f"{other}.started")
    postgres.prepare_test_database()
    (scratch / f"{me}.prepared").touch()
    wait(f"{other}.prepared")
    assert asyncio.run(use()) == 1
    (scratch / f"{me}.used").touch()
    wait(f"{other}.used")
    postgres.drop_test_database()
    (scratch / f"{me}.dropped").touch()
    wait(f"{other}.dropped")
    print(postgres.pg_database())
    """
)


@pytest.mark.parametrize("worker", [None, "gw0"], ids=["serial", "xdist"])
def test_two_simultaneous_runs_in_one_checkout(tmp_path: Path, worker: str | None) -> None:
    # Two runs of one checkout, both serial (`main`) or both xdist worker
    # `gw0`: each prepares, uses its database after the other prepared, and
    # drops only its own.
    checkout = uuid.uuid4().hex[:10]
    env = {**os.environ, "JENTIC_PG_CHECKOUT": checkout}
    env.pop("PYTEST_XDIST_WORKER", None)
    if worker is not None:
        env["PYTEST_XDIST_WORKER"] = worker
    runs = [
        subprocess.Popen(
            [sys.executable, "-c", _RUN_SCRIPT, me, other, str(tmp_path)],
            cwd=postgres.REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for me, other in (("a", "b"), ("b", "a"))
    ]
    results = [run.communicate(timeout=300) for run in runs]
    for run, (_, err) in zip(runs, results, strict=True):
        assert run.returncode == 0, err[-3000:]
    names = [out.strip().splitlines()[-1] for out, _ in results]
    assert len(set(names)) == 2
    assert all(postgres.TEST_DATABASE.fullmatch(name) for name in names)
    assert not any(_exists(name) for name in names)


def test_cleaning_drops_only_this_checkouts_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    mine, other = uuid.uuid4().hex[:10], uuid.uuid4().hex[:10]
    my_db, other_db = _test_name(mine), _test_name(other)
    _create(my_db)
    _create(other_db)
    template = postgres.template_name(postgres.template_key())
    try:
        monkeypatch.setenv("JENTIC_PG_CHECKOUT", mine)
        assert postgres.clean_test_databases() == [my_db]
        assert not _exists(my_db)
        assert _exists(other_db)
        assert _exists(template)
    finally:
        _drop(other_db)


def test_a_database_in_use_is_kept_unless_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = uuid.uuid4().hex[:10]
    name = _test_name(mine)
    _create(name)
    monkeypatch.setenv("JENTIC_PG_CHECKOUT", mine)

    async def hold_and_clean() -> tuple[list[str], list[str]]:
        config = postgres._maintenance_config(name)
        engine = create_async_engine(get_database_url(config))
        conn = await engine.connect()
        try:
            await conn.execute(text("SELECT 1"))
            kept = await asyncio.to_thread(postgres.clean_test_databases)
            forced = await asyncio.to_thread(postgres.clean_test_databases, force=True)
            return kept, forced
        finally:
            # The forced drop terminated this session: closing it raises.
            with contextlib.suppress(Exception):
                await conn.close()
            await engine.dispose()

    kept, forced = asyncio.run(hold_and_clean())
    assert kept == [] and forced == [name]
    assert not _exists(name)


def test_pruning_reclaims_only_databases_no_run_owns() -> None:
    checkout = uuid.uuid4().hex[:10]
    dead, alive, kept, foreign = (_test_name(checkout, w) for w in ("gw0", "gw1", "gw2", "gw3"))
    _create(dead, stamp=f"jentic test database {int(time.time())} 999999999")
    _create(alive, stamp=_live_stamp())
    _create(kept, stamp=f"jentic test kept {int(time.time())}")
    _create(foreign)  # no stamp: something else made it

    try:
        asyncio.run(_prune_now())
        assert not _exists(dead)
        assert _exists(alive) and _exists(kept) and _exists(foreign)
    finally:
        _drop(alive, kept, foreign)


def test_a_kept_database_outlives_its_run_and_blocks_no_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = uuid.uuid4().hex[:10]
    env = {**os.environ, "JENTIC_PG_CHECKOUT": checkout, "JENTIC_PG_KEEP_DATABASES": "1"}
    env.pop(fixture_lock.HELD_ENV, None)
    script = (
        "from tests.harness import postgres; postgres.prepare_test_database(); "
        "postgres.drop_test_database(); print(postgres.pg_database())"
    )
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=postgres.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    kept = run.stdout.strip().splitlines()[-1]
    try:
        # Its process is gone; a later preparation (this one) does not reclaim it.
        monkeypatch.setenv("JENTIC_PG_CHECKOUT", uuid.uuid4().hex[:10])
        asyncio.run(_prune_now())
        assert _exists(kept)
        assert kept not in (postgres.destroy_blockers() or [])
        monkeypatch.setenv("JENTIC_PG_CHECKOUT", checkout)
        assert postgres.clean_test_databases() == [kept]
    finally:
        _drop(kept)


async def _prune_now() -> None:
    engine = postgres._maintenance()
    try:
        async with engine.connect() as conn:
            await postgres._prune(
                conn,
                keep=postgres.template_name(postgres.template_key()),
                current=postgres.pg_database(),
            )
    finally:
        await engine.dispose()


def test_destroy_refuses_while_any_run_owns_a_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    idle = _test_name(uuid.uuid4().hex[:10])
    _create(idle, stamp=_live_stamp())
    try:
        assert postgres.destroy(["false"], force=False) == 1
        out = capsys.readouterr().out
        assert idle in out
        # This run's own database blocks too.
        assert postgres.pg_database() in out
        # Forced, the command runs.
        assert postgres.destroy([sys.executable, "-c", "pass"], force=True) == 0
    finally:
        _drop(idle)


def test_a_preparation_waits_for_a_destroy_in_progress(tmp_path: Path) -> None:
    # The destroy holds the fixture lock across its check and its command, so
    # a run starting meanwhile prepares only once the command finished.
    marker = tmp_path / "destroyed"
    command = [
        sys.executable,
        "-c",
        "import pathlib, sys, time; time.sleep(2); "
        "pathlib.Path(sys.argv[1]).write_text(str(time.time()))",
        str(marker),
    ]
    env = {**os.environ, "JENTIC_PG_CHECKOUT": uuid.uuid4().hex[:10]}
    env.pop(fixture_lock.HELD_ENV, None)
    script = (
        "import time; from tests.harness import postgres; postgres.prepare_test_database(); "
        "print(time.time()); postgres.drop_test_database()"
    )
    result: dict[str, int] = {}
    destroyer = threading.Thread(
        target=lambda: result.update(rc=postgres.destroy(command, force=True))
    )
    destroyer.start()
    time.sleep(0.5)
    prepared = subprocess.run(
        [sys.executable, "-c", script],
        cwd=postgres.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    destroyer.join()
    assert result["rc"] == 0
    assert prepared.returncode == 0, prepared.stderr[-3000:]
    assert float(prepared.stdout.strip()) > float(marker.read_text())


def test_incompatible_shared_roles_fail_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(postgres.ROLES, "registry_user", "not-the-shared-password")
    with pytest.raises(RuntimeError, match="its own server"):
        asyncio.run(postgres.check_roles(postgres.pg_database()))
