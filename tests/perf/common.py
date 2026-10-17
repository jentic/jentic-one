"""Shared helpers for the broker perf suite (stdlib only).

Imported both by host-side scripts (``uv run python -m tests.perf.<script>``)
and by the Locust workers running in the ``locustio/locust`` image, so it must
not depend on anything beyond the standard library.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PERF_DIR: Final = Path(__file__).resolve().parent
REPO_ROOT: Final = PERF_DIR.parent.parent
RESULTS_DIR: Final = PERF_DIR / "results"
COMPOSE_FILE: Final = PERF_DIR / "docker-compose.perf.yaml"
DEFAULT_ENV_FILE: Final = RESULTS_DIR / ".perf-env.json"
LOCK_FILE: Final = RESULTS_DIR / ".perf.lock"

#: Minimum share of expected-status responses for a measurement to count.
#: Fast failures (401, 429, 5xx) return in a few ms without reaching the
#: upstream, so a mostly-failed run would otherwise read as *lower* overhead.
DEFAULT_MIN_OK: Final = 0.99
#: Tokens must outlive the run by at least this much (``check_fresh``).
TOKEN_MARGIN_S: Final = 300.0

#: In-network addresses (what load generators inside the compose network use).
BROKER_INTERNAL: Final = "http://broker:8000"
APP_INTERNAL: Final = "http://app:8000"
UPSTREAM_PORT: Final = 8084
#: Every harness alias lives under this suffix (allowlisted in config.perf.yaml).
ALIAS_DOMAIN: Final = "perf.internal"

#: Harness route every credential-type comparison drives (uniform, always 200).
ECHO_PATH: Final = "/behavior/echo"
#: GET counterpart for GET-method runs.
GET_PATH: Final = "/servers/resolution"

#: Variant knobs recorded with every result (mirrors docker-compose.perf.yaml).
VARIANT_KEYS: Final = (
    "PERF_STREAMING",
    "PERF_MAX_PER_HOST",
    "PERF_RETRY",
    "PERF_BREAKER",
    "PERF_RATE_LIMIT",
    "PERF_MAX_IN_FLIGHT",
    "PERF_RESOLVE_TTL",
    "PERF_TOOLKIT_TTL",
    "PERF_RULE_TTL",
    "PERF_CREDENTIAL_TTL",
    "PERF_ADMIN_POOL",
    "PERF_CONTROL_POOL",
    "PERF_REGISTRY_POOL",
    "PERF_TRACING",
    "PERF_WORKERS",
    "PERF_BROKER_CPUS",
    "PERF_APP_CPUS",
    "PERF_PG_CPUS",
    "PERF_UPSTREAM_CPUS",
    "PERF_LOADGEN_CPUS",
    "PERF_IMAGE",
)
DEFAULT_IMAGE: Final = "jentic-one/app:perf"


@dataclass(frozen=True)
class Vendor:
    """One perf vendor: its own API (own upstream alias) + one credential type."""

    slug: str
    alias: str
    credential_type: str
    #: For api_key credentials: header | query | cookie.
    location: str | None = None


#: One vendor per credential type so no two active credentials ever share an
#: API (which the resolver would reject as ambiguous), and one alias per vendor
#: so no two APIs share a (host, method, path) (ambiguous URL match).
VENDORS: Final[tuple[Vendor, ...]] = (
    Vendor("perf-noauth", "smoke-noauth", "no_auth"),
    Vendor("perf-bearer", "smoke-bearer", "bearer_token"),
    Vendor("perf-apikey-hdr", "smoke-apikey-hdr", "api_key", "header"),
    Vendor("perf-apikey-qry", "smoke-apikey-qry", "api_key", "query"),
    Vendor("perf-apikey-cookie", "smoke-apikey-cookie", "api_key", "cookie"),
    Vendor("perf-sigv4", "smoke-sigv4", "sigv4"),
    *(Vendor(f"perf-up-{i}", f"smoke-upstream-{i}", "no_auth") for i in range(1, 6)),
)


@dataclass
class Caller:
    """A broker caller credential (what goes in ``Authorization: Bearer``)."""

    kind: str  # jak | at | jwt
    token: str
    agent_id: str
    expires_at: float | None = None
    #: Unix time the token was minted (``None`` = unknown); with ``expires_at``
    #: it gives the token's full lifetime.
    issued_at: float | None = None


class TokenLifetimeTooShortError(SystemExit):
    """``check_fresh``: the run outlasts a token's whole lifetime.

    Re-provisioning can't fix this; the run has to be shortened (or the TTLs
    raised). A ``SystemExit`` so CLI entry points still exit with the message;
    wrappers that give their own fix hint catch this type, not the text.
    """


@dataclass
class PerfEnv:
    """Everything the load generators need, written by ``provision.py``."""

    app_url: str
    broker_url: str
    upstream_url: str
    agent_id: str
    callers: dict[str, Caller] = field(default_factory=dict)
    #: vendor slug -> {"credential_id", "api": {vendor,name,version}, "alias"}
    vendors: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Extra agents (jak_ keys) bound to perf-noauth, for the rate-limit scenario.
    pool_agents: list[Caller] = field(default_factory=list)
    admin_token: str = ""
    #: Unix time the admin token expires (``None`` = unknown, treated as stale).
    admin_expires_at: float | None = None
    #: Unix time the admin token was minted (``None`` = unknown).
    admin_issued_at: float | None = None
    created_at: str = ""

    def caller(self, kind: str = "jak") -> Caller:
        return self.callers[kind]

    def check_fresh(
        self,
        *,
        callers: Iterable[str] | None = None,
        admin: bool = False,
        run_s: float = 0.0,
        margin_s: float = TOKEN_MARGIN_S,
        now: float | None = None,
    ) -> None:
        """Refuse to start if a token the run uses expires before it can finish.

        ``callers`` names the caller kinds the run drives (``None`` = all), and
        ``admin`` whether it uses the admin token. Each must stay valid for
        ``run_s`` (the planned run length) plus ``margin_s``. An expired token
        turns every call into a fast 401, which would otherwise be measured.

        When a stale token's full lifetime is itself shorter than the run,
        re-provisioning can't help, so it raises ``TokenLifetimeTooShortError``
        (saying to shorten the run) instead of a plain ``SystemExit``.
        """
        needed = run_s + margin_s
        deadline = (time.time() if now is None else now) + needed
        kinds = list(self.callers) if callers is None else list(callers)
        tokens = [
            (f"{kind} caller", self.callers[kind].expires_at, self.callers[kind].issued_at)
            for kind in kinds
            if self.callers[kind].expires_at is not None
        ]
        if admin:
            tokens.append(("admin token", self.admin_expires_at, self.admin_issued_at))
        stale = [(label, exp, iat) for label, exp, iat in tokens if exp is None or exp < deadline]
        if not stale:
            return
        listed = ", ".join(f"{label} (expires {_fmt_ts(exp)})" for label, exp, _ in stale)
        too_long = [
            label
            for label, exp, iat in stale
            if exp is not None and iat is not None and exp - iat < needed
        ]
        if too_long:
            raise TokenLifetimeTooShortError(
                f"the planned run (~{run_s / 60:.0f} min +{margin_s:.0f}s margin) is longer "
                f"than the token lifetime of: {', '.join(too_long)} — split it with --only "
                "or lower --repeat (or raise the TTLs in config.perf.yaml)"
            )
        raise SystemExit(
            f"tokens expire before this run can finish (+{margin_s:.0f}s margin): "
            f"{listed} — re-run `make perf-provision`"
        )

    def save(self, path: Path = DEFAULT_ENV_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path | None = None) -> PerfEnv:
        target = path or Path(os.environ.get("PERF_ENV_FILE", str(DEFAULT_ENV_FILE)))
        raw = json.loads(target.read_text())
        raw["callers"] = {k: Caller(**v) for k, v in raw.get("callers", {}).items()}
        raw["pool_agents"] = [Caller(**v) for v in raw.get("pool_agents", [])]
        return cls(**raw)


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def upstream_base(alias: str) -> str:
    """In-network base URL of a harness alias."""
    return f"http://{alias}.{ALIAS_DOMAIN}:{UPSTREAM_PORT}"


def broker_target(alias: str, path: str = ECHO_PATH, broker: str = BROKER_INTERNAL) -> str:
    """The broker proxy URL for an upstream alias + path (full URL in the path)."""
    return f"{broker.rstrip('/')}/{upstream_base(alias)}{path}"


def _run(cmd: list[str]) -> str | None:
    """stdout of ``cmd``, or ``None`` if it can't run or exits non-zero."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def git_sha() -> str:
    return _run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"]) or "unknown"


def git_dirty() -> bool | None:
    """Uncommitted changes in the host checkout (``None`` = git unavailable)."""
    out = _run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"])
    return None if out is None else bool(out)


def image_info(image: str) -> dict[str, str | None]:
    """Id + build time of the image under test (it may be older than HEAD)."""
    out = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}\t{{.Created}}"])
    image_id, _, created = (out or "").partition("\t")
    return {"ref": image, "id": image_id or None, "created": created or None}


def service_images() -> dict[str, str]:
    """Image reference per compose service (tag@digest pins = tool versions)."""
    out = _run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            *("--profile", "tools", "--profile", "load", "--profile", "obs"),
            "config",
            "--format",
            "json",
        ]
    )
    if not out:
        return {}
    services = json.loads(out).get("services", {})
    return {name: str(svc.get("image", "")) for name, svc in sorted(services.items())}


def variant_snapshot(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The PERF_* knobs in effect (env, then explicit overrides)."""
    snap = {k: os.environ[k] for k in VARIANT_KEYS if k in os.environ}
    snap.update(overrides or {})
    return snap


def run_metadata(scenario: str, overrides: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "git_sha": git_sha(),
        "git_dirty": git_dirty(),
        "app_image": image_info(os.environ.get("PERF_IMAGE", DEFAULT_IMAGE)),
        "images": service_images(),
        "timestamp": datetime.now(UTC).isoformat(),
        "variant": variant_snapshot(overrides),
    }


def write_result(scenario: str, payload: dict[str, Any]) -> Path:
    """Write one result JSON under results/ and return its path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}-{scenario}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def compose(*args: str, env: dict[str, str] | None = None, capture: bool = False) -> str:
    """Run ``docker compose -f docker-compose.perf.yaml <args>``."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    merged = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, env=merged, capture_output=capture, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr if capture else ""
        raise RuntimeError(f"docker compose {' '.join(args)} failed ({proc.returncode}) {detail}")
    return proc.stdout if capture else ""


def wait_healthy(url: str, timeout_s: float = 90.0) -> None:
    """Poll ``url`` until it answers 2xx (used after recreating the broker)."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    return
                last = f"HTTP {resp.status}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(1)
    raise TimeoutError(f"{url} not healthy after {timeout_s}s: {last}")


@contextlib.contextmanager
def exclusive_run(tool: str) -> Iterator[None]:
    """Hold ``results/.perf.lock`` for the run; refuse to start if another tool has it.

    The host-side tools recreate the broker and stop/start ``app``, so two at
    once would measure (and reconfigure) each other's stack.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            holder = fh.read().strip() or "unknown"
            raise SystemExit(f"another perf tool is running ({holder}); wait for it") from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"{tool} pid={os.getpid()}")
        fh.flush()
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


RestoreStep = tuple[str, Callable[[], object]]


def _restore(steps: Iterable[RestoreStep]) -> list[str]:
    failed = []
    for label, step in steps:
        try:
            step()
        except Exception as exc:
            print(f"restore: {label} failed: {exc!r}", file=sys.stderr, flush=True)
            failed.append(label)
    return failed


@contextlib.contextmanager
def restore_on_exit(*steps: RestoreStep) -> Iterator[None]:
    """Run every restore step on exit, each independently of the others' outcome.

    Steps are closures, so they see state the body updated (e.g. the variant
    the broker is on). A failing step is logged and the rest still run. If the
    body raised, its exception propagates unchanged; otherwise a failed step
    raises after all steps ran, so a half-restored stack is never silent.
    """
    try:
        yield
    except BaseException:
        _restore(steps)
        raise
    failed = _restore(steps)
    if failed:
        raise RuntimeError(f"restore failed: {', '.join(failed)} — check `docker compose ps`")
