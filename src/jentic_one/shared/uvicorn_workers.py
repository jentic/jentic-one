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
adds one rule: a worker that dies before it ever started serving stops the
whole supervisor, which exits with ``STARTUP_FAILURE`` like a single-worker run.

"Started serving" is reported by the worker itself (its pid on a shared queue
once uvicorn's startup completes), not inferred from its exit code, so a worker
killed by a signal during boot (the cgroup OOM killer's ``SIGKILL`` is the usual
one) counts as a startup failure too.
"""

from __future__ import annotations

import functools
import multiprocessing
import os
import sys
from collections.abc import Callable
from multiprocessing.queues import SimpleQueue
from socket import socket

import structlog
from uvicorn import Config, Server
from uvicorn.main import STARTUP_FAILURE
from uvicorn.supervisors.multiprocess import Multiprocess

logger = structlog.get_logger(__name__)


class _WorkerServer(Server):
    """uvicorn ``Server`` that reports its pid once startup has completed.

    ``started_queue`` is assigned in the worker process by :func:`_run_worker`;
    the supervisor reads it to tell a worker that served from one that never did.
    """

    started_queue: SimpleQueue[int] | None = None

    async def startup(self, sockets: list[socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self.started_queue is not None:
            self.started_queue.put(os.getpid())


def _run_worker(
    server: _WorkerServer,
    sockets: list[socket] | None = None,
    *,
    started_queue: SimpleQueue[int],
) -> None:
    """Worker-process target: serve, exiting ``STARTUP_FAILURE`` if serving never began.

    uvicorn's own worker target returns normally after a lifespan startup
    failure and exits 1 when the app factory fails, so the supervisor cannot
    tell a boot failure from a crash. Any exit before ``server.started`` is a
    startup failure here.
    """
    server.started_queue = started_queue
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
        *,
        started_queue: SimpleQueue[int],
    ) -> None:
        super().__init__(config, target=target, sockets=sockets)
        self.startup_failed = False
        self._started_queue = started_queue
        self._started_pids: set[int] = set()

    def _drain_started(self) -> None:
        while not self._started_queue.empty():
            self._started_pids.add(self._started_queue.get())

    def keep_subprocess_alive(self) -> None:
        if self.should_exit.is_set():
            return
        # Drain before the liveness check: a worker that reported and then died
        # in the same poll interval did serve, and is respawned below.
        self._drain_started()
        for process in self.processes:
            child = process.process
            if child.is_alive() or child.pid in self._started_pids:
                continue
            logger.error(
                "uvicorn_worker_startup_failed",
                pid=child.pid,
                exitcode=child.exitcode,
                detail=(
                    "a worker process died before it started serving; stopping "
                    "every worker and exiting with the startup-failure code "
                    "instead of respawning it. The worker's own log lines above "
                    "name the cause; a negative exitcode is the signal that killed "
                    "it (-9 is usually the out-of-memory killer)."
                ),
            )
            self.startup_failed = True
            self.should_exit.set()
            return
        super().keep_subprocess_alive()
        # Forget pids of replaced workers so a reused pid cannot inherit "started".
        self._started_pids &= {process.pid for process in self.processes if process.pid is not None}


def run_workers(app: str, *, host: str, port: int, workers: int) -> None:
    """Serve the ``app`` factory import string from ``workers`` processes.

    Exits the process with ``STARTUP_FAILURE`` (3) when any worker dies before
    it starts serving, including a worker respawned after a crash and a worker
    killed by a signal during boot.
    """
    config = Config(app, host=host, port=port, factory=True, workers=workers)
    server = _WorkerServer(config=config)
    sock = config.bind_socket()
    # Workers are spawn-context processes (uvicorn's ``get_subprocess``), so the
    # queue must come from the same context to be inherited by them.
    started_queue: SimpleQueue[int] = multiprocessing.get_context("spawn").SimpleQueue()
    supervisor = FailFastMultiprocess(
        config,
        target=functools.partial(_run_worker, server, started_queue=started_queue),
        sockets=[sock],
        started_queue=started_queue,
    )
    try:
        supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        started_queue.close()
    if supervisor.startup_failed:
        sys.exit(STARTUP_FAILURE)
