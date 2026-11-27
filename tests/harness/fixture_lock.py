"""A host-side lock per fixture Postgres server, shared by every checkout.

``make start-fixtures`` (``scripts/setup.sh``), ``make destroy-fixtures`` and
the test harness's database work (``tests/harness/postgres.py``) all hold it,
so a destroy never removes a volume between another checkout's check and its
use, and two preparations never build, clone or prune at once. A Postgres
advisory lock cannot cover the destroy: ``docker compose down`` ends the
connection that would hold it.

The lock is a file under ``$XDG_CACHE_HOME/jentic`` (``~/.cache/jentic``),
named after the server's port: the tests always reach it on ``localhost``.

``python -m tests.harness.fixture_lock -- <command>…`` runs a command holding
it; the command sees ``JENTIC_FIXTURE_LOCK_HELD=1`` and does not take it again.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

HELD_ENV = "JENTIC_FIXTURE_LOCK_HELD"


def _port(port: int | None) -> int:
    return port if port is not None else int(os.environ.get("JENTIC_PG_PORT", "5432"))


def lock_path(port: int | None = None) -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "jentic" / f"fixtures-localhost-{_port(port)}.lock"


@contextlib.contextmanager
def held(port: int | None = None) -> Iterator[None]:
    """Hold the server's lock, waiting for it; re-entrant across a process tree via the env."""
    if os.environ.get(HELD_ENV) == "1":
        yield
        return
    path = lock_path(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            file.seek(0)
            holder = file.read().strip() or "another process"
            print(f"waiting for the fixture lock {path} ({holder})", file=sys.stderr, flush=True)
            fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        file.truncate()
        file.write(f"pid {os.getpid()}: {' '.join(sys.argv)}\n")
        file.flush()
        os.environ[HELD_ENV] = "1"
        try:
            yield
        finally:
            os.environ.pop(HELD_ENV, None)
            file.seek(0)
            file.truncate()
            fcntl.flock(file, fcntl.LOCK_UN)


def run_locked(command: list[str]) -> int:
    """Run *command* holding the lock; its exit status."""
    with held():
        return subprocess.run(command, check=False, env={**os.environ, HELD_ENV: "1"}).returncode


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        sys.exit("usage: python -m tests.harness.fixture_lock -- <command>…")
    sys.exit(run_locked(args))


if __name__ == "__main__":
    main()
