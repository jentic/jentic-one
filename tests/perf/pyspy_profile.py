"""CPU-profile the broker under closed-loop load with py-spy.

A pinned ``python`` slim sidecar joins the broker container's PID namespace
(``--pid=container:… --cap-add SYS_PTRACE``) and installs a pinned, hash-checked
py-spy wheel *before* the load starts (so a slow PyPI never eats into the timed
window), then samples PID 1 (``--subprocesses`` follows uvicorn workers). The
broker image is untouched. Meanwhile oha drives ``-c N`` closed-loop load
through the broker.

Outputs in ``results/pyspy/``: the raw collapsed stacks and a flamegraph SVG,
recorded concurrently over the same window, plus a JSON summary with CPU
share per subsystem plus the hottest frames. The
subsystem buckets are first-match on the stack, in the order of ``BUCKETS``.

The profile (flamegraph, raw stacks and summary JSON) is only kept if the load
ran for the whole window and at least ``--min-ok`` of its responses were 200.
Any run that ends without writing its summary (rejected load, py-spy failure
or timeout, Ctrl-C, unreadable stacks) deletes the sidecar's files, since they
would profile an idle (or failing) broker or be incomplete.

Usage::

    uv run python -m tests.perf.pyspy_profile                  # 30 s, c=16, perf-noauth
    uv run python -m tests.perf.pyspy_profile --vendor perf-bearer --concurrency 32
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests.perf.bench_overhead import (
    Case,
    check_ok,
    oha_args,
    run_oha,
    start_oha,
    stop_oha,
    summarise,
)
from tests.perf.common import (
    DEFAULT_MIN_OK,
    ECHO_PATH,
    RESULTS_DIR,
    PerfEnv,
    compose,
    exclusive_run,
    restore_on_exit,
    run_metadata,
    upstream_base,
    write_result,
)

OUT_DIR = RESULTS_DIR / "pyspy"
#: Sidecar image and py-spy release, pinned so profiles are comparable run to run.
PYSPY_IMAGE = (
    "python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36"
)
PYSPY_VERSION = "0.4.2"
#: PyPI sha256 of the py-spy 0.4.2 Linux wheels (x86_64, aarch64): pip refuses
#: anything else, since the sidecar holds SYS_PTRACE in the broker's namespace.
PYSPY_HASHES = (
    "aeb0323409199c785f730645e9f4bb7a7b9ca2c481f2c331a55642b5d13fa52f",  # pragma: allowlist secret
    "142887e984a4e541071c99a4401ff8c3770f255d329dbd0f64e8c1dd51882cce",  # pragma: allowlist secret
)

# (bucket, substrings) — first match wins, so specific beats generic.
BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dns getaddrinfo", ("getaddrinfo",)),
    ("runtime imports", ("_find_and_load",)),
    ("db: pre-ping", ("_async_ping", "do_ping")),
    ("db: selectin child loads", ("selectin", "orm/strategies.py")),
    ("db: begin/commit/rollback", ("transaction.py", "_commit_impl", "_rollback_impl")),
    ("db: orm flush", ("unitofwork", "persistence.py")),
    ("db: other sqlalchemy/asyncpg", ("sqlalchemy", "asyncpg")),
    ("otel", ("opentelemetry", "trace/__init__", "contextvars_context")),
    ("logging", ("structlog", "logging/__init__")),
    ("upstream http client", ("httpcore", "httpx", "h11")),
    ("pydantic", ("pydantic",)),
    ("asgi stack", ("uvicorn", "starlette", "fastapi", "anyio")),
)

# Inclusive share for call sites worth tracking across runs.
PROBES: dict[str, tuple[str, ...]] = {
    "egress DnsPinningTransport": ("shared/egress.py",),
    "validate_upstream_url": ("validate_upstream_url",),
    "credential list_by_vendor": ("list_by_vendor",),
    "authorization / rules": ("authorization.py",),
    "registry resolve": ("url_lookup.py", "url_index_repo.py"),
    "streaming persist": ("_persist_streaming_outcome",),
    "emit_event": ("emit_event",),
}


def broker_container() -> str:
    cid = compose("ps", "-q", "broker", capture=True).strip()
    if not cid:
        raise SystemExit("broker is not running (make perf-up)")
    return cid


#: Per-process name, so one tool's cleanup can never remove another's sidecar.
PYSPY_CONTAINER = f"jentic-perf-pyspy-{os.getpid()}"


def start_pyspy(cid: str) -> None:
    """One sidecar in the broker's PID namespace, py-spy installed before any load."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stop_pyspy()
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            PYSPY_CONTAINER,
            f"--pid=container:{cid}",
            "--cap-add",
            "SYS_PTRACE",
            "-v",
            f"{OUT_DIR}:/out",
            PYSPY_IMAGE,
            "sleep",
            "infinity",
        ],
        check=True,
        capture_output=True,
    )
    requirement = f"py-spy=={PYSPY_VERSION} " + " ".join(f"--hash=sha256:{h}" for h in PYSPY_HASHES)
    subprocess.run(
        [
            "docker",
            "exec",
            PYSPY_CONTAINER,
            "sh",
            "-c",
            f"echo '{requirement}' > /tmp/req.txt && "
            "pip install -q --only-binary=:all: --require-hashes -r /tmp/req.txt",
        ],
        check=True,
        capture_output=True,
    )


def stop_pyspy() -> None:
    subprocess.run(["docker", "rm", "-f", PYSPY_CONTAINER], capture_output=True, check=False)


def record(args: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(["docker", "exec", PYSPY_CONTAINER, "py-spy", "record", *args.split()])


def analyse(raw: Path, top: int = 25) -> dict[str, Any]:
    total = 0
    buckets: collections.Counter[str] = collections.Counter()
    leaves: collections.Counter[str] = collections.Counter()
    probes: collections.Counter[str] = collections.Counter()
    for line in raw.read_text().splitlines():
        stack, _, count = line.rpartition(" ")
        n = int(count)
        total += n
        leaf = stack.rsplit(";", 1)[-1]
        if leaf.startswith("run (asyncio/runners.py"):
            # Self time in the loop's C core: epoll + callback dispatch.
            bucket = "event loop (self)"
        else:
            bucket = next((b for b, keys in BUCKETS if any(k in stack for k in keys)), "other")
        buckets[bucket] += n
        for name, keys in PROBES.items():
            if any(k in stack for k in keys):
                probes[name] += n
        leaves[leaf] += n
    share = {k: round(v / total, 4) for k, v in buckets.most_common()} if total else {}
    return {
        "samples": total,
        "buckets": share,
        "probes": {k: round(v / total, 4) for k, v in probes.most_common()} if total else {},
        "top_self": [
            {"frame": k, "share": round(v / total, 4)} for k, v in leaves.most_common(top)
        ],
    }


class LoadError(RuntimeError):
    """The load generator did not drive the broker for the profiled window."""


def check_load(
    result: dict[str, Any], errors: list[BaseException], alive: bool, min_ok: float
) -> None:
    """Raise unless the background load finished cleanly with enough 200s."""
    if errors:
        raise LoadError(f"load generator failed: {errors[0]!r}") from errors[0]
    if alive:
        raise LoadError("load generator still running after the profile window")
    if not result:
        raise LoadError("load generator produced no result")
    if why := check_ok(result, min_ok, "load"):
        raise LoadError(why)


def discard_profile(stem: str) -> None:
    """Delete one run's raw stacks and flamegraph (the sidecar writes them first)."""
    for suffix in (".txt", ".svg"):
        (OUT_DIR / f"{stem}{suffix}").unlink(missing_ok=True)


def check_load_or_discard(
    stem: str, result: dict[str, Any], errors: list[BaseException], alive: bool, min_ok: float
) -> None:
    """``check_load``, deleting the run's profile files if the load was not valid.

    Otherwise ``results/pyspy/`` would keep an idle (or failing) broker's
    flamegraph next to the valid ones.
    """
    try:
        check_load(result, errors, alive, min_ok)
    except LoadError:
        discard_profile(stem)
        raise


@contextlib.contextmanager
def discard_on_failure(stem: str) -> Iterator[None]:
    """Delete the run's profile files if anything in the block raises.

    Covers every way a run can end without a summary: a py-spy timeout or
    non-zero exit, a rejected load, Ctrl-C, or ``analyse`` failing on
    truncated stacks. The exception propagates unchanged.
    """
    try:
        yield
    except BaseException:
        discard_profile(stem)
        raise


def capture(
    *,
    vendor: str,
    caller: str,
    concurrency: int,
    duration: int,
    rate: int,
    min_ok: float = DEFAULT_MIN_OK,
) -> tuple[str, dict[str, Any]]:
    """Record ``duration`` s of py-spy under load; returns (stem, oha summary).

    Raises ``LoadError`` unless the load ran for the whole window with at least
    ``min_ok`` 200s. Any failure (rejected load, py-spy timeout or failure,
    Ctrl-C) deletes that run's flamegraph and raw stacks first, so callers never
    analyse (or keep) a profile of an idle broker. Callers that analyse the
    returned stem wrap that in ``discard_on_failure`` too.
    """
    env = PerfEnv.load()
    url = f"{env.broker_url}/{upstream_base(env.vendors[vendor]['alias'])}{ECHO_PATH}"
    token = env.caller(caller).token
    stem = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{vendor}-c{concurrency}"
    # Raw (for the bucket table) and SVG recorded concurrently over the same
    # window; --nonblocking samplers only read memory, so they don't stack up
    # pauses on the broker.
    warm_s, load_s = 5, 5 + duration + 5
    # Sidecar start (pip install) + the load window.
    env.check_fresh(callers=[caller], run_s=load_s + 120)
    result: dict[str, Any] = {}
    errors: list[BaseException] = []

    def load() -> None:
        try:
            a = oha_args(
                url, Case("profile"), token, n=None, conns=concurrency, rate=None,
                duration_s=load_s,
            )  # fmt: skip
            result.update(summarise(run_oha(a), 200))
        except BaseException as exc:
            errors.append(exc)

    # restore_on_exit sits inside discard_on_failure, so the sidecars are gone
    # (and py-spy can't write any more) before a failed run's files are deleted.
    with (
        exclusive_run("pyspy_profile"),
        discard_on_failure(stem),
        restore_on_exit(("remove oha sidecar", stop_oha), ("remove py-spy sidecar", stop_pyspy)),
    ):
        cid = broker_container()
        print(f"starting py-spy sidecar (pip install py-spy=={PYSPY_VERSION}) ...", flush=True)
        start_pyspy(cid)
        start_oha()
        loader = threading.Thread(target=load, daemon=True)
        loader.start()
        time.sleep(warm_s)
        common = f"--pid 1 --subprocesses --nonblocking --duration {duration} --rate {rate}"
        procs = [
            record(f"{common} --format raw --output /out/{stem}.txt"),
            record(f"{common} --output /out/{stem}.svg"),
        ]
        codes = [proc.wait(timeout=duration + 60) for proc in procs]
        if any(codes):
            raise RuntimeError(f"py-spy record failed: exit codes {codes}")
        loader.join(timeout=load_s + 30)
        check_load_or_discard(stem, result, errors, loader.is_alive(), min_ok)
    return stem, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="py-spy the broker under closed-loop load")
    parser.add_argument("--vendor", default="perf-noauth")
    parser.add_argument("--caller", choices=("jak", "at", "jwt"), default="jak")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=int, default=30, help="py-spy seconds")
    parser.add_argument("--rate", type=int, default=200, help="py-spy samples/s")
    parser.add_argument(
        "--min-ok",
        type=float,
        default=DEFAULT_MIN_OK,
        help="minimum 200 share under load for the profile to count (default %(default)s)",
    )
    args = parser.parse_args(argv)

    stem, result = capture(
        vendor=args.vendor,
        caller=args.caller,
        concurrency=args.concurrency,
        duration=args.duration,
        rate=args.rate,
        min_ok=args.min_ok,
    )
    with discard_on_failure(stem):
        summary = analyse(OUT_DIR / f"{stem}.txt")
        print(f"load: {result['rps']:.1f} rps  p50={result['p50_ms']:.1f}ms")
        for name, share in summary["buckets"].items():
            print(f"  {share:6.1%}  {name}")
        print("  -- inclusive")
        for name, share in summary["probes"].items():
            print(f"  {share:6.1%}  {name}")
        path = write_result(
            "profile",
            {
                **run_metadata("profile"),
                "vendor": args.vendor,
                "caller": args.caller,
                "concurrency": args.concurrency,
                "pyspy_version": PYSPY_VERSION,
                "load": result,
                "flamegraph": f"pyspy/{stem}.svg",
                "profile": summary,
            },
        )
    print(f"wrote {path} and results/pyspy/{stem}.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
