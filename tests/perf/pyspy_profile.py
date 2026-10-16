"""CPU-profile the broker under closed-loop load with py-spy.

A ``python:3.12-slim`` sidecar joins the broker container's PID namespace
(``--pid=container:… --cap-add SYS_PTRACE``) and installs py-spy *before* the
load starts (so a slow PyPI never eats into the timed window), then samples
PID 1 (``--subprocesses`` follows uvicorn workers). The broker image is
untouched. Meanwhile oha drives ``-c N`` closed-loop load through the broker.

Outputs in ``results/pyspy/``: the raw collapsed stacks and a flamegraph SVG,
recorded concurrently over the same window, plus a JSON summary with CPU
share per subsystem plus the hottest frames. The
subsystem buckets are first-match on the stack, in the order of ``BUCKETS``.

Usage::

    uv run python -m tests.perf.pyspy_profile                  # 30 s, c=16, perf-noauth
    uv run python -m tests.perf.pyspy_profile --vendor perf-bearer --concurrency 32
"""

from __future__ import annotations

import argparse
import collections
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from tests.perf.bench_overhead import Case, oha_args, run_oha, start_oha, stop_oha, summarise
from tests.perf.common import (
    ECHO_PATH,
    RESULTS_DIR,
    PerfEnv,
    compose,
    run_metadata,
    upstream_base,
    write_result,
)

OUT_DIR = RESULTS_DIR / "pyspy"
PYSPY_IMAGE = "python:3.12-slim"

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


PYSPY_CONTAINER = "jentic-perf-pyspy"


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
    subprocess.run(
        ["docker", "exec", PYSPY_CONTAINER, "pip", "install", "-q", "py-spy"],
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="py-spy the broker under closed-loop load")
    parser.add_argument("--vendor", default="perf-noauth")
    parser.add_argument("--caller", choices=("jak", "at", "jwt"), default="jak")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=int, default=30, help="py-spy seconds")
    parser.add_argument("--rate", type=int, default=200, help="py-spy samples/s")
    args = parser.parse_args(argv)

    env = PerfEnv.load()
    url = f"{env.broker_url}/{upstream_base(env.vendors[args.vendor]['alias'])}{ECHO_PATH}"
    token = env.caller(args.caller).token
    stem = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{args.vendor}-c{args.concurrency}"
    cid = broker_container()
    # Raw (for the bucket table) and SVG recorded concurrently over the same
    # window; --nonblocking samplers only read memory, so they don't stack up
    # pauses on the broker.
    warm_s, load_s = 5, 5 + args.duration + 5
    result: dict[str, Any] = {}

    def load() -> None:
        a = oha_args(
            url, Case("profile"), token, n=None, conns=args.concurrency, rate=None,
            duration_s=load_s,
        )  # fmt: skip
        result.update(summarise(run_oha(a), 200))

    print("starting py-spy sidecar (pip install py-spy) ...", flush=True)
    try:
        # Inside the try so a failed/interrupted start still removes both sidecars.
        start_pyspy(cid)
        start_oha()
        loader = threading.Thread(target=load, daemon=True)
        loader.start()
        time.sleep(warm_s)
        common = (
            f"--pid 1 --subprocesses --nonblocking --duration {args.duration} --rate {args.rate}"
        )
        procs = [
            record(f"{common} --format raw --output /out/{stem}.txt"),
            record(f"{common} --output /out/{stem}.svg"),
        ]
        codes = [proc.wait(timeout=args.duration + 60) for proc in procs]
        if any(codes):
            raise RuntimeError(f"py-spy record failed: exit codes {codes}")
        loader.join(timeout=load_s + 30)
    finally:
        stop_oha()
        stop_pyspy()

    summary = analyse(OUT_DIR / f"{stem}.txt")
    print(f"load: {result.get('rps', 0):.1f} rps  p50={result.get('p50_ms', 0):.1f}ms")
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
            "load": result,
            "flamegraph": f"pyspy/{stem}.svg",
            "profile": summary,
        },
    )
    print(f"wrote {path} and results/pyspy/{stem}.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
