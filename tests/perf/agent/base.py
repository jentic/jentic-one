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
from collections.abc import Iterable, Iterator, Sequence
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
    exclusive_run,
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


#: What the broker image is built from (``git_dirty`` / doctor provenance).
SRC_PATHS: Final = ("src", "pyproject.toml", "uv.lock")


def git_dirty(paths: Sequence[str] = SRC_PATHS) -> bool:
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
    #: ``PERF_*`` overrides the probe applied on top of the environment.
    variant: dict[str, str] = field(default_factory=dict)
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
            "variant": variant_snapshot(self.variant),
        }
        payload = {
            **meta,
            "result": self.result,
            "highlights": self.highlights,
            "data": self.data,
        }
        path.write_text(json.dumps(payload, indent=2, default=str))
        try:
            rel: Path | str = path.relative_to(REPO_ROOT)
        except ValueError:
            rel = path
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


def require_ok(load: dict[str, Any], what: str) -> None:
    """Refuse a load run where any request failed: its numbers describe the failure."""
    ratio = load.get("ok_ratio")
    if ratio is None or ratio < 1.0:
        raise PrereqError(
            f"{what}: not every request returned 200 (ok_ratio {ratio}, "
            f"statuses {load.get('status_codes')}, errors {load.get('errors')})",
            "make perf-agent-doctor   # stale tokens, or a broken broker",
        )


def env_or_fail() -> PerfEnv:
    try:
        return PerfEnv.load()
    except FileNotFoundError as exc:
        raise PrereqError(
            "tests/perf/results/.perf-env.json missing", "make perf-up && make perf-provision"
        ) from exc


def require_fresh(
    env: PerfEnv, *, callers: Iterable[str] | None, run_s: float, admin: bool = False
) -> None:
    """Refuse a probe whose tokens expire before it can finish (fast 401s, not data)."""
    try:
        env.check_fresh(callers=callers, admin=admin, run_s=run_s)
    except SystemExit as exc:
        raise PrereqError(str(exc.code), "make perf-provision") from None


@contextlib.contextmanager
def run_lock(tool: str) -> Iterator[None]:
    """Hold the suite-wide run lock, so no other perf tool reconfigures the stack meanwhile."""
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(exclusive_run(tool))
        except SystemExit as exc:
            raise PrereqError(str(exc.code), "wait for the other perf tool to finish") from None
        yield


def proxy_url(env: PerfEnv, vendor: str = "perf-noauth", path: str = ECHO_PATH) -> str:
    """Host-side broker URL (published port) for a perf vendor."""
    return f"{BROKER_HOST}/{upstream_base(env.vendors[vendor]['alias'])}{path}"


def internal_url(env: PerfEnv, vendor: str = "perf-noauth", path: str = ECHO_PATH) -> str:
    """In-network broker URL (what oha / Locust use)."""
    return f"{env.broker_url}/{upstream_base(env.vendors[vendor]['alias'])}{path}"


def recreate_broker(overrides: dict[str, str]) -> None:
    """Recreate the broker with ``overrides``; harness failures become :class:`PrereqError`."""
    # Docker creates a missing bind-mount source root-owned on Linux engines.
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **overrides}
    try:
        compose("up", "-d", "--force-recreate", "--no-deps", "--wait", "broker", env=env)
        wait_healthy(f"{BROKER_HOST}/health")
    except (RuntimeError, TimeoutError) as exc:
        raise PrereqError(
            f"broker did not come up: {str(exc)[-300:]}",
            "docker compose -f tests/perf/docker-compose.perf.yaml logs broker; then make perf-up",
        ) from exc


#: Container env the trace hook reads; empty = clean broker.
TRACE_ENV_KEYS: Final = ("PERF_HOOKS_PATH", "PERF_TRACE", "PERF_TRACE_FUNCS", "PERF_TRACE_PARAMS")
#: Trace outputs (event JSONL + the hook's patch manifest) under ``TRACE_DIR``.
TRACE_GLOBS: Final = ("*.jsonl", "*.hook.json")


def purge_traces() -> None:
    """Delete trace files. Only call while no traced broker holds them open."""
    for pattern in TRACE_GLOBS:
        for path in TRACE_DIR.glob(pattern):
            path.unlink(missing_ok=True)


@contextlib.contextmanager
def broker_variant(
    overrides: dict[str, str], *, trace: str = "", trace_params: bool = False
) -> Iterator[None]:
    """Recreate the broker with ``overrides`` (+ the trace hook), restore clean on exit.

    The clean restore runs whether the variant broker came up or not, so a
    failed start never leaves the stack traced. Traces can hold raw SQL bind
    parameters, so they are deleted once the traced broker is gone.
    """
    traced: dict[str, str] = {}
    if trace:
        traced = {"PERF_HOOKS_PATH": "/perf-hooks", "PERF_TRACE": trace}
        if trace_params:
            traced["PERF_TRACE_PARAMS"] = "1"
    clean = dict.fromkeys(TRACE_ENV_KEYS, "")
    if trace:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        # The broker runs as uid 10001: it may create files (write + search)
        # but not list or delete other users' files (sticky, no read for others).
        TRACE_DIR.chmod(0o1733)
        purge_traces()
    try:
        recreate_broker({**clean, **overrides, **traced})
        yield
    finally:
        recreate_broker(clean)
        if trace:
            purge_traces()


def hook_manifest() -> dict[str, list[str]]:
    """Patch targets of the traced broker(s), merged across worker pids."""
    merged: dict[str, set[str]] = {"targets": set(), "patched": set(), "failed": set()}
    for path in TRACE_DIR.glob("*.hook.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for key, values in merged.items():
            values.update(data.get(key, []))
    out = {k: sorted(v) for k, v in merged.items()}
    out["missing"] = sorted(merged["targets"] - merged["patched"])
    return out


def require_patched(manifest: dict[str, list[str]], required: Sequence[str]) -> None:
    """Refuse to report when the hook could not wrap a target the analysis needs."""
    missing = [r for r in required if r not in manifest["patched"]]
    if missing:
        raise PrereqError(
            f"trace hook did not patch {missing} (failed: {manifest['failed'] or 'none'}); "
            "the target was renamed or never imported",
            "update DEFAULT_STAGES in tests/perf/agent/hooks/sitecustomize.py to the new name",
        )


def hook_stats(events: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Latest swallowed-error / dropped-event counters the hook reported."""
    stats = {"errors": 0, "dropped": 0}
    for e in events:
        if e.get("ev") == "hook_stats":
            stats = {k: max(stats[k], int(e.get(k, 0))) for k in stats}
    return stats


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
