"""Multi-worker uvicorn supervisor that exits when a worker cannot start.

``uvicorn.run(..., workers=N)`` hands the workers to uvicorn's ``Multiprocess``
supervisor, which respawns any dead worker every half second without limit and
skips the ``STARTUP_FAILURE`` (3) exit that a single-worker run makes. A fatal
startup error (unreachable database, bad encryption keyset, missing optional
dependency, a config file that no longer validates) then becomes a hot respawn
loop inside a container that stays ``Running``, so the orchestrator's restart
backoff never engages.

:func:`run_workers` keeps uvicorn's supervisor (socket sharing, signal
forwarding, SIGHUP restart, respawn of a worker that dies after it served) and
adds one rule: a worker that exits before it ever started serving stops the
whole supervisor, which exits with ``STARTUP_FAILURE`` like a single-worker run.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from socket import socket

import structlog
from uvicorn import Config, Server
from uvicorn.main import STARTUP_FAILURE
from uvicorn.supervisors.multiprocess import Multiprocess

logger = structlog.get_logger(__name__)


def _run_worker(server: Server, sockets: list[socket] | None = None) -> None:
    """Worker-process target: serve, exiting ``STARTUP_FAILURE`` if serving never began.

    uvicorn's own worker target returns normally after a lifespan startup
    failure and exits 1 when the app factory fails, so the supervisor cannot
    tell a boot failure from a crash. Any exit before ``server.started`` is a
    startup failure here.
    """
    try:
        server.run(sockets=sockets)
    except SystemExit:
        if server.started:
            raise
        sys.exit(STARTUP_FAILURE)
    except Exception:
        if server.started:
            raise
        logger.exception("uvicorn_worker_startup_error")
        sys.exit(STARTUP_FAILURE)
    if not server.started:
        sys.exit(STARTUP_FAILURE)


class FailFastMultiprocess(Multiprocess):
    """uvicorn ``Multiprocess`` that stops instead of respawning a worker that failed to start."""

    def __init__(
        self,
        config: Config,
        target: Callable[[list[socket] | None], None],
        sockets: list[socket],
    ) -> None:
        super().__init__(config, target=target, sockets=sockets)
        self.startup_failed = False

    def keep_subprocess_alive(self) -> None:
        if self.should_exit.is_set():
            return
        for process in self.processes:
            child = process.process
            if not child.is_alive() and child.exitcode == STARTUP_FAILURE:
                logger.error(
                    "uvicorn_worker_startup_failed",
                    pid=child.pid,
                    detail=(
                        "a worker process exited before it started serving; stopping "
                        "every worker and exiting with the startup-failure code "
                        "instead of respawning it. The worker's own log lines above "
                        "name the cause."
                    ),
                )
                self.startup_failed = True
                self.should_exit.set()
                return
        super().keep_subprocess_alive()


def run_workers(app: str, *, host: str, port: int, workers: int) -> None:
    """Serve the ``app`` factory import string from ``workers`` processes.

    Exits the process with ``STARTUP_FAILURE`` (3) when any worker fails to
    start, including a worker respawned after a crash.
    """
    config = Config(app, host=host, port=port, factory=True, workers=workers)
    server = Server(config=config)
    sock = config.bind_socket()
    supervisor = FailFastMultiprocess(
        config, target=functools.partial(_run_worker, server), sockets=[sock]
    )
    try:
        supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    if supervisor.startup_failed:
        sys.exit(STARTUP_FAILURE)
