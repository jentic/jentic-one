"""Tests for the multi-worker supervisor (``shared/uvicorn_workers.py``).

Each test runs the supervisor in a child interpreter: it installs its own
signal handlers and spawns worker processes, neither of which belongs inside
the pytest process.
"""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from uvicorn.main import STARTUP_FAILURE

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT_S = 60


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _supervisor(factory: str, port: int, workers: int = 2) -> subprocess.Popen[bytes]:
    script = (
        "from jentic_one.shared.uvicorn_workers import run_workers\n"
        f"run_workers('tests.unit._uvicorn_worker_apps:{factory}', "
        f"host='127.0.0.1', port={port}, workers={workers})\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.parametrize("factory", ["failing_factory", "failing_lifespan_factory"])
def test_worker_startup_failure_exits_with_startup_failure_code(factory: str) -> None:
    """uvicorn's own supervisor respawns such a worker forever; this one stops and exits 3."""
    proc = _supervisor(factory, _free_port())
    try:
        assert proc.wait(timeout=_TIMEOUT_S) == STARTUP_FAILURE
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_healthy_workers_serve_and_stop_cleanly_on_sigterm() -> None:
    port = _free_port()
    proc = _supervisor("healthy_factory", port)
    try:
        deadline = time.monotonic() + _TIMEOUT_S
        while True:
            assert proc.poll() is None, "supervisor exited before serving"
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                    assert resp.status == 200
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=_TIMEOUT_S) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
