"""Shared plumbing for the agent-facing perf probes (``make perf-agent-*``).

Every probe builds a :class:`Report` and calls :meth:`Report.finish`, which
prints agent-readable markdown (run header, tables, mechanical highlights,
``NEXT:`` hints) and writes the full data as JSON. The last stdout line is
always ``ARTIFACT: <path>``. Exit codes: 0 ran, 2 prerequisite/harness failure,
3 regression (``compare`` only).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from tests.perf.common import (
    COMPOSE_FILE,
    ECHO_PATH,
    REPO_ROOT,
    RESULTS_DIR,
    PerfEnv,
    compose,
    git_sha,
    upstream_base,
    variant_snapshot,
    wait_healthy,
)

AGENT_DIR: Final = RESULTS_DIR / "agent"
TRACE_DIR: Final = AGENT_DIR / "trace"
BROKER_PORT: Final = os.environ.get("PERF_BROKER_PORT", "18100")
BROKER_HOST: Final = f"http://127.0.0.1:{BROKER_PORT}"
EXIT_PREREQ: Final = 2
EXIT_REGRESSION: Final = 3


class PrereqError(RuntimeError):
    """A missing prerequisite; ``fix`` is the command that resolves it."""

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(message)
        self.fix = fix


# --------------------------------------------------------------------------- markdown


def fmt(value: Any, digits: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], digits: int = 2) -> str:
    def cell(v: Any) -> str:
        return fmt(v, digits).replace("|", "\\|").replace("\n", " ")

    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def git_dirty(paths: Sequence[str] = ("src", "pyproject.toml", "uv.lock")) -> bool:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(out.stdout.strip())


@dataclass
class Report:
    """One probe's output: markdown sections for stdout + a JSON artifact."""

    cmd: str
    title: str
    mode: str = "clean"
    sections: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    #: Optional verdict printed as ``RESULT: ...`` just before the artifact line.
    result: str = ""

    def section(self, heading: str, body: str, note: str = "") -> None:
        text = f"## {heading}\n\n"
        if note:
            text += f"_{note}_\n\n"
        self.sections.append(text + body)

    def finish(self) -> Path:
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = AGENT_DIR / f"{stamp}-{self.cmd}.json"
        meta = {
            "cmd": self.cmd,
            "git_sha": git_sha(),
            "src_dirty": git_dirty(),
            "timestamp": datetime.now(UTC).isoformat(),
            "broker_mode": self.mode,
            "variant": variant_snapshot(),
        }
        payload = {
            **meta,
            "result": self.result,
            "highlights": self.highlights,
            "data": self.data,
        }
        path.write_text(json.dumps(payload, indent=2, default=str))
        rel = path.relative_to(REPO_ROOT)
        dirty = " (src has uncommitted changes)" if meta["src_dirty"] else ""
        print(f"# {self.title}\n")
        print(
            f"git `{meta['git_sha']}`{dirty} · broker mode **{self.mode}** · {meta['timestamp']}\n"
        )
        if self.highlights:
            print("## Highlights\n")
            print("\n".join(f"- {h}" for h in self.highlights) + "\n")
        for s in self.sections:
            print(s + "\n")
        if self.next_steps:
            print("NEXT:")
            print("\n".join(f"- `{n}`" for n in self.next_steps))
            print()
        if self.result:
            print(f"RESULT: {self.result}")
        print(f"ARTIFACT: {rel}")
        return path


# --------------------------------------------------------------------------- stack helpers


def env_or_fail() -> PerfEnv:
    try:
        return PerfEnv.load()
    except FileNotFoundError as exc:
        raise PrereqError(
            "tests/perf/results/.perf-env.json missing", "make perf-up && make perf-provision"
        ) from exc


def proxy_url(env: PerfEnv, vendor: str = "perf-noauth", path: str = ECHO_PATH) -> str:
    """Host-side broker URL (published port) for a perf vendor."""
    return f"{BROKER_HOST}/{upstream_base(env.vendors[vendor]['alias'])}{path}"


def internal_url(env: PerfEnv, vendor: str = "perf-noauth", path: str = ECHO_PATH) -> str:
    """In-network broker URL (what oha / Locust use)."""
    return f"{env.broker_url}/{upstream_base(env.vendors[vendor]['alias'])}{path}"


def recreate_broker(overrides: dict[str, str]) -> None:
    env = {**os.environ, **overrides}
    compose("up", "-d", "--force-recreate", "--no-deps", "--wait", "broker", env=env)
    wait_healthy(f"{BROKER_HOST}/health")


TRACE_ENV_KEYS: Final = ("PERF_HOOKS_PATH", "PERF_TRACE", "PERF_TRACE_FUNCS")


@contextlib.contextmanager
def broker_variant(overrides: dict[str, str], *, trace: str = "") -> Iterator[None]:
    """Recreate the broker with ``overrides`` (+ the trace hook), restore clean on exit."""
    traced = {"PERF_HOOKS_PATH": "/perf-hooks", "PERF_TRACE": trace} if trace else {}
    clean = dict.fromkeys(TRACE_ENV_KEYS, "")
    if trace:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        # The broker runs as uid 10001; let it write into the bind mount.
        TRACE_DIR.chmod(0o777)
        AGENT_DIR.chmod(0o777)
    recreate_broker({**clean, **overrides, **traced})
    try:
        yield
    finally:
        recreate_broker(clean)


class TraceTail:
    """Reads trace events appended after construction (never deletes live files).

    The broker keeps its JSONL open; unlinking it would leave the process
    writing to a deleted inode, so we remember per-file offsets instead.
    """

    def __init__(self) -> None:
        self._offsets = {p: p.stat().st_size for p in TRACE_DIR.glob("*.jsonl")}

    def read(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in TRACE_DIR.glob("*.jsonl"):
            with path.open() as fh:
                fh.seek(self._offsets.get(path, 0))
                for line in fh:
                    if line.strip():
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        events.sort(key=lambda e: e.get("t", 0.0))
        return events


def wait_quiet(seconds: float = 1.5) -> None:
    """Let post-response background work (streaming persistence) land."""
    time.sleep(seconds)


def pct(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return float(ordered[idx])


def compose_file() -> str:
    return str(COMPOSE_FILE)
