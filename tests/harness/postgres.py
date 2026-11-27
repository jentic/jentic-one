"""Connection settings and bootstrap helpers for the local Docker Postgres fixture.

Shared by ``tests/integration`` and ``tests/web`` so both harnesses target the
same server. The fixture is the one ``make start-fixtures`` brings up from
``docker/local-setup`` (one schema and role per surface). Its host port is read
from ``JENTIC_PG_PORT``, the variable that compose file publishes Postgres on,
defaulting to ``5432``.

Every test process gets its own database, so tests that truncate or count
shared tables never race another process, and any number of runs, in one
checkout or in several (git worktrees), share one Postgres server:

- **A template per migration set.** ``jentic_tpl_<key>`` is created from
  ``template0``, given this checkout's init SQL
  (``docker/local-setup/init-schemas.sql``) and migrated by the deployment
  runner, ``python -m jentic_one.migrations.run``, as ``make start-fixtures``
  migrates ``jentic``. ``key`` (:func:`template_key`) hashes everything that
  decides the migrated database: every ``jentic_one`` source file, the init
  SQL, ``alembic.ini``, ``uv.lock`` and the effective migration configuration
  (:func:`migration_config`). Runs with the same inputs share it. It is marked
  a template that takes no connections, so no app or test ever holds it open.
- **A database per process.** ``jentic_test_<checkout>_<run>_<worker>`` is
  cloned from the template (``CREATE DATABASE … TEMPLATE``) when a test
  session starts. ``<checkout>`` hashes this checkout's path, ``<run>`` is
  random per process and ``<worker>`` is the xdist worker, or ``main``.
- **Shared roles.** Schemas and grants live in each database, but roles belong
  to the server, so every checkout on one server uses the roles :data:`ROLES`
  names. A template build checks they log in before migrating.
- **One lock.** Building, cloning, pruning and cleaning hold the host-side
  fixture lock (``tests/harness/fixture_lock.py``) that ``make start-fixtures``
  and ``make destroy-fixtures`` take too. The server is always ``localhost``,
  so a host-side lock serializes every process that uses it.
- **Teardown.** Each process drops its database when its test session ends
  (:func:`drop_test_database`). With ``JENTIC_PG_KEEP_DATABASES=1`` it stamps
  it kept instead, and only ``make clean-test-databases`` drops it. A database
  whose process is gone, or that is older than :data:`STALE_DATABASE_S`, is
  dropped by the next preparation. Old templates are pruned too: the newest
  :data:`KEEP_TEMPLATES` and any built in the last :data:`TEMPLATE_GRACE_S`
  stay. Nothing in use is dropped without ``force``.

The dev database ``jentic`` that ``make start-fixtures`` migrates is not used
by the tests, and is shared by every checkout on the server.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.db.session import get_database_url
from tests.harness import fixture_lock

DB_NAMES: tuple[str, ...] = ("registry", "admin", "control")

# A deterministic 32-byte AES key (base64). Tests that create credentials need
# a non-empty encryption keyset or the Context boot fails with
# "EncryptionConfig.entries must not be empty".
TEST_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

# The server-wide roles every checkout on one server shares, as the init SQL
# creates them (local-dev passwords).
ROLES: dict[str, str] = {f"{surface}_user": f"{surface}_pass" for surface in DB_NAMES}

_PG_HOST = "localhost"
_PG_SUPERUSER = "postgres"
_PG_SUPERUSER_PASSWORD = "postgres"  # pragma: allowlist secret

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_SQL = Path("docker") / "local-setup" / "init-schemas.sql"
MIGRATION_CONFIG = Path("config") / "local.yaml"
TEMPLATE_PREFIX = "jentic_tpl_"
TEST_PREFIX = "jentic_test_"
# Hashed into the key: bump it when the key's algorithm changes, so templates
# keyed the other way are not reused.
KEY_VERSION = 1
# Templates kept beyond the one in use, and how long a new one is kept.
KEEP_TEMPLATES = 3
TEMPLATE_GRACE_S = 900
# A test database older than this is reclaimed even if its process looks alive.
STALE_DATABASE_S = 86400
_CHECKOUT_ENV = "JENTIC_PG_CHECKOUT"
_KEEP_ENV = "JENTIC_PG_KEEP_DATABASES"
_CHECKOUT = re.compile(r"^[a-z0-9]{1,12}$")
_WORKER = re.compile(r"^[a-z0-9]{1,12}$")
TEST_DATABASE = re.compile(r"^jentic_test_([a-z0-9]{1,12})_([0-9a-f]{12})_([a-z0-9]{1,12})$")
TEMPLATE_DATABASE = re.compile(r"^jentic_tpl_[0-9a-f]{12}$")
# Per-worker test databases that fixture volumes created before per-run names
# may still hold; only ``clean --all`` drops them.
_LEGACY_DATABASE = re.compile(r"^jentic_gw\d+$")
# Database comments: ``template <built>``, ``database <created> <pid>`` (a
# run's) and ``kept <created>`` (kept for debugging).
_STAMP = re.compile(r"^jentic test (template|database|kept) (\d+)(?: (\d+))?$")
# SQLSTATEs: a database other sessions are using; a server still starting.
_OBJECT_IN_USE = "55006"
_CANNOT_CONNECT_NOW = "57P03"
_MIGRATION_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TZ",
    "SYSTEMROOT",
    "VIRTUAL_ENV",
)

# This process's run id: two test runs never share a database name.
_RUN = secrets.token_hex(6)
# The database this process prepared, if any. Both the integration and the web
# ``_apply_migrations`` fixtures ask for it, and the second ask must not clone
# it again.
_prepared: set[str] = set()


def encryption_config() -> dict[str, Any]:
    """``credentials`` config section holding the single deterministic test key."""
    return {
        "encryption": {
            "active_id": "v1",
            "entries": [{"id": "v1", "material": TEST_ENCRYPTION_KEY}],
        },
    }


def pg_port() -> int:
    """Fixture Postgres host port: ``JENTIC_PG_PORT``, as docker/local-setup publishes it."""
    return int(os.environ.get("JENTIC_PG_PORT", "5432"))


def xdist_worker() -> str | None:
    """The ``pytest-xdist`` worker id (``gw0``, ``gw1``, …), or ``None`` when not distributed."""
    return os.environ.get("PYTEST_XDIST_WORKER") or None


def checkout_id() -> str:
    """This checkout's short id: ``JENTIC_PG_CHECKOUT``, else a hash of its path."""
    override = os.environ.get(_CHECKOUT_ENV, "").strip()
    if override:
        if not _CHECKOUT.fullmatch(override):
            raise ValueError(f"{_CHECKOUT_ENV} must be 1 to 12 lower-case letters or digits")
        return override
    return hashlib.sha256(str(REPO_ROOT).encode()).hexdigest()[:8]


def pg_database() -> str:
    """Database this process tests against: ``jentic_test_<checkout>_<run>_<worker>``."""
    worker = xdist_worker() or "main"
    if not _WORKER.fullmatch(worker):
        raise ValueError(f"unexpected xdist worker id {worker!r}")
    return f"{TEST_PREFIX}{checkout_id()}_{_RUN}_{worker}"


def _databases(name: str, port: int) -> dict[str, dict[str, Any]]:
    """The ``databases`` config section for database *name*: the tests' and the migrations'."""
    return {
        surface: {
            "backend": "postgres",
            "host": _PG_HOST,
            "port": port,
            "name": name,
            "user": f"{surface}_user",
            "password": ROLES[f"{surface}_user"],
            "schema_name": surface,
        }
        for surface in DB_NAMES
    }


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def migration_config(name: str, port: int, root: Path = REPO_ROOT) -> dict[str, Any]:
    """The whole config the migration runner reads to migrate database *name*.

    ``config/local.yaml`` (what ``make start-fixtures`` migrates ``jentic``
    with), with the ``databases`` and credential-encryption sections replaced
    by exactly the ones the tests connect with (:func:`postgres_config`).
    """
    path = root / MIGRATION_CONFIG
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    return _merge(
        loaded if isinstance(loaded, dict) else {},
        {"databases": _databases(name, port), "credentials": encryption_config()},
    )


def template_inputs(root: Path = REPO_ROOT) -> list[Path]:
    """Every file that decides the migrated database, so the template key changes with it.

    All of ``src/jentic_one``, not only the migrations: schemas, server
    defaults and upgrade steps reach modules anywhere in it, and a spurious
    rebuild costs one migration run where a stale template costs wrong tests.
    Plus the fixture's init SQL, ``alembic.ini`` and ``uv.lock``.
    """
    files = set((root / "src" / "jentic_one").rglob("*.py"))
    for extra in (Path("alembic.ini"), INIT_SQL, Path("uv.lock")):
        if (root / extra).is_file():
            files.add(root / extra)
    return sorted(files)


def template_key(root: Path = REPO_ROOT) -> str:
    """sha256 over :data:`KEY_VERSION`, the effective migration config and :func:`template_inputs`.

    The config's database name and port are placeholders: the template's own
    name derives from the key, and a run or a server must not change it.
    """
    digest = hashlib.sha256(f"jentic test template key v{KEY_VERSION}\0".encode())
    config = migration_config("<database>", 0, root)
    digest.update(json.dumps(config, sort_keys=True, default=str).encode() + b"\0")
    for path in template_inputs(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def template_name(key: str) -> str:
    return f"{TEMPLATE_PREFIX}{key[:12]}"


def postgres_config(database: str | None = None) -> AppConfig:
    """AppConfig for the local Docker Postgres fixture (one schema per surface).

    ``database`` defaults to :func:`pg_database` (this process's database).
    """
    databases = _databases(database or pg_database(), pg_port())
    for section in databases.values():
        section["pool_max"] = 2
    return AppConfig.model_validate({"databases": databases, "credentials": encryption_config()})


def superuser_config(db_config: DatabaseConfig) -> DatabaseConfig:
    """``db_config`` connecting as the fixture's bootstrap superuser."""
    return db_config.model_copy(
        update={"user": _PG_SUPERUSER, "password": SecretStr(_PG_SUPERUSER_PASSWORD)}
    )


def prepare_test_database() -> None:
    """Give this process its own database, cloned from the template for this checkout's inputs.

    Builds the template first when no run has built it for these inputs.
    A no-op on a second call in the same process.
    """
    target = pg_database()
    if target in _prepared:
        return
    with fixture_lock.held(pg_port()):
        asyncio.run(_prepare(template_name(template_key()), target))
    _prepared.add(target)


def drop_test_database() -> None:
    """Drop this process's database at the end of its session, or stamp it kept.

    Forced: a connection something left open must not keep the database. With
    ``JENTIC_PG_KEEP_DATABASES=1`` it stays, stamped kept, so no preparation
    reclaims it and no destroy waits for it.
    """
    target = pg_database()
    if target not in _prepared:
        return
    if os.environ.get(_KEEP_ENV, "").strip() == "1":
        asyncio.run(_keep(target))
    else:
        asyncio.run(_drop(target))
    _prepared.discard(target)


def clean_test_databases(*, every_checkout: bool = False, force: bool = False) -> list[str]:
    """Drop this checkout's test databases (or every checkout's, every template and legacy names).

    Unless *force*, a database in use is left, never terminated. Returns the
    names dropped.
    """
    with fixture_lock.held(pg_port()):
        return asyncio.run(_clean(every_checkout=every_checkout, force=force))


def _maintenance_config(name: str = "postgres") -> DatabaseConfig:
    return DatabaseConfig.model_validate(
        {
            "host": _PG_HOST,
            "port": pg_port(),
            "name": name,
            "user": _PG_SUPERUSER,
            "password": _PG_SUPERUSER_PASSWORD,
        }
    )


def _maintenance(name: str = "postgres") -> AsyncEngine:
    """An AUTOCOMMIT engine as the superuser: CREATE/DROP DATABASE cannot run in a transaction."""
    return create_async_engine(
        get_database_url(_maintenance_config(name)), isolation_level="AUTOCOMMIT"
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


def _selected_for_clean(name: str, *, checkout: str, every_checkout: bool) -> bool:
    match = TEST_DATABASE.fullmatch(name)
    if match is not None:
        return every_checkout or match[1] == checkout
    return every_checkout and bool(
        TEMPLATE_DATABASE.fullmatch(name) or _LEGACY_DATABASE.fullmatch(name)
    )


async def _clean(*, every_checkout: bool, force: bool) -> list[str]:
    engine = _maintenance()
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT datname FROM pg_database"))).all()
            checkout = checkout_id()
            dropped = []
            for (name,) in rows:
                if not _selected_for_clean(name, checkout=checkout, every_checkout=every_checkout):
                    continue
                if await _drop_unused(conn, name, force=force):
                    dropped.append(name)
            return dropped
    finally:
        await engine.dispose()


async def _drop(name: str) -> None:
    engine = _maintenance()
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


async def _keep(name: str) -> None:
    engine = _maintenance()
    try:
        async with engine.connect() as conn:
            await _stamp(conn, name, f"kept {int(time.time())}")
    finally:
        await engine.dispose()


async def _stamp(conn: AsyncConnection, name: str, stamp: str) -> None:
    # Every stamp is built here from digits and fixed words: nothing to escape.
    await conn.execute(text(f"COMMENT ON DATABASE \"{name}\" IS 'jentic test {stamp}'"))


async def _prepare(template: str, target: str) -> None:
    """Build *template* if needed, clone *target* from it, then prune."""
    engine = _maintenance()
    try:
        async with engine.connect() as conn:
            await _ensure_template(conn, template)
            await _clone_database(conn, template, target)
            await _prune(conn, keep=template, current=target)
    finally:
        await engine.dispose()


async def _ensure_template(conn: AsyncConnection, template: str) -> None:
    """Build *template* at head unless a finished one exists (one marked a template)."""
    row = (
        await conn.execute(
            text("SELECT datistemplate FROM pg_database WHERE datname = :name"),
            {"name": template},
        )
    ).first()
    if row is not None and row[0]:
        return
    if row is not None:
        # A build that did not finish (its process died): start again.
        await conn.execute(text(f'DROP DATABASE "{template}" WITH (FORCE)'))
    await conn.execute(text(f'CREATE DATABASE "{template}" TEMPLATE template0'))
    await apply_init_sql(template)
    await check_roles(template)
    await asyncio.to_thread(run_migrations, template)
    await _stamp(conn, template, f"template {int(time.time())}")
    await conn.execute(
        text(f'ALTER DATABASE "{template}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false')
    )


async def apply_init_sql(database: str, root: Path = REPO_ROOT) -> None:
    """Run the fixture's init SQL in *database*, as the Postgres image runs it on first boot."""
    engine = create_async_engine(get_database_url(_maintenance_config(database)))
    try:
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            driver: Any = raw.driver_connection
            # The simple-query protocol: the file holds several statements.
            await driver.execute(
                "SET client_min_messages TO warning;\n"
                + (root / INIT_SQL).read_text(encoding="utf-8")
            )
    finally:
        await engine.dispose()


def migration_env(config_file: Path) -> dict[str, str]:
    """The migration runner's environment: the process basics and *config_file*, nothing else.

    No inherited ``JENTIC_*`` override reaches it, so it migrates exactly the
    database :func:`migration_config` describes.
    """
    env = {name: os.environ[name] for name in _MIGRATION_ENV if name in os.environ}
    env["JENTIC_CONFIG_FILE"] = str(config_file)
    return env


def run_migrations(database: str) -> None:
    """Migrate *database* with the deployment runner, as ``make start-fixtures`` does.

    The deployment runner, not Alembic directly: a full upgrade also runs the
    upgrade steps and the service-account retirement. It reads
    :func:`migration_config`, written to a private file.
    """
    with tempfile.TemporaryDirectory(prefix="jentic-test-migrate-") as scratch:
        config_file = Path(scratch) / "config.yaml"
        config_file.write_text(yaml.safe_dump(migration_config(database, pg_port())))
        result = subprocess.run(
            [sys.executable, "-m", "jentic_one.migrations.run"],
            cwd=REPO_ROOT,
            env=migration_env(config_file),
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"migrating the {database!r} test template failed (exit {result.returncode}):\n"
            f"{result.stdout[-4000:]}{result.stderr[-4000:]}"
        )


async def _clone_database(conn: AsyncConnection, template: str, target: str) -> None:
    """Create *target* as a copy of *template*, stamped with this process as its owner."""
    try:
        await conn.execute(text(f'CREATE DATABASE "{target}" TEMPLATE "{template}"'))
    except DBAPIError as exc:
        raise RuntimeError(
            f"could not clone the {template!r} test template into {target!r}: {exc}"
        ) from exc
    await _stamp(conn, target, f"database {int(time.time())} {os.getpid()}")


async def check_roles(database: str) -> None:
    """Check every role in :data:`ROLES` logs in to *database* with its password.

    Roles are shared by every database on the server, so a checkout cannot
    give them other definitions: it needs a server of its own.
    """
    failures = []
    for role, password in ROLES.items():
        config = _maintenance_config(database).model_copy(
            update={"user": role, "password": SecretStr(password)}
        )
        engine = create_async_engine(get_database_url(config))
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except DBAPIError as exc:
            failures.append(f"{role}: {exc.orig}")
        finally:
            await engine.dispose()
    if failures:
        raise RuntimeError(
            f"the fixture server on localhost:{pg_port()} does not have the roles the tests "
            "share across checkouts (docker/local-setup/init-schemas.sql):\n  "
            + "\n  ".join(failures)
            + "\nA checkout that needs other role definitions needs its own server: set "
            "JENTIC_FIXTURES_PROJECT and JENTIC_PG_PORT for `make start-fixtures` and the run."
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owned_by_a_live_run(comment: str | None, *, now: float | None = None) -> bool:
    """Whether a test database stamped *comment* belongs to a run that is still going.

    A run's stamp names its pid on this host (the server is always
    ``localhost``). It stops counting once that pid is gone or the stamp is
    older than :data:`STALE_DATABASE_S`. Kept and unstamped databases are no
    run's.
    """
    match = _STAMP.fullmatch(comment or "")
    if match is None or match[1] != "database" or match[3] is None:
        return False
    if (now if now is not None else time.time()) - int(match[2]) > STALE_DATABASE_S:
        return False
    return _pid_alive(int(match[3]))


def reclaimable(comment: str | None, *, now: float | None = None) -> bool:
    """Whether a preparation may drop a test database stamped *comment*: a run's, now over.

    Kept and unstamped databases are never reclaimed: someone chose to keep
    them, or something else made them.
    """
    match = _STAMP.fullmatch(comment or "")
    if match is None or match[1] != "database":
        return False
    return not owned_by_a_live_run(comment, now=now)


async def _prune(conn: AsyncConnection, *, keep: str, current: str) -> None:
    """Drop old templates and the test databases no run owns.

    Kept: *keep*, the newest :data:`KEEP_TEMPLATES` other templates and any
    built in the last :data:`TEMPLATE_GRACE_S`; every test database whose
    run lives or that is stamped kept. A database in use is skipped.
    """
    rows = (
        await conn.execute(
            text(
                "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database "
                "WHERE datname LIKE 'jentic\\_%'"
            )
        )
    ).all()
    now = time.time()
    templates = []
    for name, comment in rows:
        match = _STAMP.fullmatch(comment or "")
        if TEMPLATE_DATABASE.fullmatch(name) and match is not None and match[1] == "template":
            if name != keep:
                templates.append((name, int(match[2])))
        elif TEST_DATABASE.fullmatch(name) and name != current and reclaimable(comment, now=now):
            await _drop_unused(conn, name)
    templates.sort(key=lambda item: item[1], reverse=True)
    for name, built in templates[KEEP_TEMPLATES:]:
        if now - built >= TEMPLATE_GRACE_S:
            await _drop_unused(conn, name)


async def _drop_unused(conn: AsyncConnection, name: str, *, force: bool = False) -> bool:
    """Drop *name* (a template too) unless a session uses it and not *force*; whether dropped."""
    await conn.execute(text(f'ALTER DATABASE "{name}" WITH IS_TEMPLATE false'))
    try:
        await conn.execute(text(f'DROP DATABASE "{name}"{" WITH (FORCE)" if force else ""}'))
    except DBAPIError as exc:
        if _sqlstate(exc) != _OBJECT_IN_USE:
            raise
        # A template in use stays one; the next prune tries again.
        if TEMPLATE_DATABASE.fullmatch(name):
            await conn.execute(text(f'ALTER DATABASE "{name}" WITH IS_TEMPLATE true'))
        return False
    return True


async def ensure_schema(db_config: DatabaseConfig) -> None:
    """Create the target schema if it doesn't already exist.

    Alembic itself does not create schemas; the migration env assumes one
    is already present. Tests using ephemeral Docker volumes need this on
    first boot. Connects as the bootstrap superuser because CREATE SCHEMA
    requires the CREATE privilege on the database.
    """
    engine = create_async_engine(get_database_url(superuser_config(db_config)))
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{db_config.schema_name}"'))
    finally:
        await engine.dispose()


def alembic_config_for(db_name: str, db_config: DatabaseConfig) -> AlembicConfig:
    """Build an in-memory Alembic config bound to ``db_name``.

    Uses the on-disk ``alembic.ini`` for script/version locations and points
    Alembic at the test database via ``sqlalchemy.url``. The migration env
    reads the URL from app config too, but setting it here keeps the Alembic
    config self-contained.
    """
    cfg = AlembicConfig("alembic.ini", ini_section=db_name)
    cfg.set_section_option(
        db_name, "sqlalchemy.url", get_database_url(db_config).render_as_string(hide_password=False)
    )
    cfg.set_section_option(db_name, "schema_name", db_config.schema_name)
    return cfg


def destroy_blockers() -> list[str] | None:
    """The test databases a live run owns, in any checkout; ``None`` when no server answers."""
    try:
        rows = asyncio.run(_test_database_stamps())
    except OSError:
        return None
    except DBAPIError as exc:
        if _sqlstate(exc) == _CANNOT_CONNECT_NOW:
            return None
        raise
    return [name for name, comment in rows if owned_by_a_live_run(comment)]


async def _test_database_stamps() -> list[tuple[str, str | None]]:
    engine = _maintenance()
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database "
                        "WHERE datname LIKE 'jentic\\_test\\_%' ORDER BY datname"
                    )
                )
            ).all()
        return [(str(name), comment) for name, comment in rows if TEST_DATABASE.fullmatch(name)]
    finally:
        await engine.dispose()


def destroy(command: list[str], *, force: bool) -> int:
    """``make destroy-fixtures``: check, then run *command* (``docker compose … down -v``).

    Both under the host-side fixture lock, so no preparation or start runs
    between the check and the removal: one that finished first leaves a
    database that blocks, one that starts later waits and finds no server.
    Returns the exit status: 1 when a live run blocks and not *force*.
    """
    with fixture_lock.held(pg_port()):
        blockers = destroy_blockers()
        if blockers is None:
            print(f"no fixture server on localhost:{pg_port()}: nothing to check")
        elif blockers and not force:
            print("refusing to destroy the fixture volume; test runs still own:")
            for blocker in blockers:
                print(f"  {blocker}")
            print("Wait for those runs, or run with FORCE=1.")
            return 1
        return subprocess.run(command, check=False).returncode if command else 0


def main(argv: list[str] | None = None) -> None:
    """``make clean-test-databases``, and ``make destroy-fixtures`` (``--destroy -- <command>``)."""
    parser = argparse.ArgumentParser(prog="python -m tests.harness.postgres")
    parser.add_argument(
        "--all",
        action="store_true",
        help="every checkout's test databases, every template and legacy names",
    )
    parser.add_argument("--force", action="store_true", help="also drop or destroy what is in use")
    parser.add_argument(
        "--destroy",
        action="store_true",
        help="check no test run uses the server, then run the command after `--`, under the lock",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.destroy:
        sys.exit(destroy(command, force=args.force))
    if command:
        parser.error("a command is only taken with --destroy")
    dropped = clean_test_databases(every_checkout=args.all, force=args.force)
    print(f"dropped {len(dropped)} test database(s) on localhost:{pg_port()}")
    for name in dropped:
        print(f"  {name}")


if __name__ == "__main__":
    main()
