"""Shared helpers for the broker perf suite (stdlib only).

Imported both by host-side scripts (``uv run python -m tests.perf.<script>``)
and by the Locust workers running in the ``locustio/locust`` image, so it must
not depend on anything beyond the standard library.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PERF_DIR: Final = Path(__file__).resolve().parent
REPO_ROOT: Final = PERF_DIR.parent.parent
RESULTS_DIR: Final = PERF_DIR / "results"
COMPOSE_FILE: Final = PERF_DIR / "docker-compose.perf.yaml"
DEFAULT_ENV_FILE: Final = RESULTS_DIR / ".perf-env.json"

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
    "PERF_ADMIN_POOL",
    "PERF_CONTROL_POOL",
    "PERF_REGISTRY_POOL",
    "PERF_TRACING",
    "PERF_WORKERS",
    "PERF_BROKER_CPUS",
    "PERF_LOADGEN_CPUS",
)


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
    created_at: str = ""

    def caller(self, kind: str = "jak") -> Caller:
        return self.callers[kind]

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


def upstream_base(alias: str) -> str:
    """In-network base URL of a harness alias."""
    return f"http://{alias}.{ALIAS_DOMAIN}:{UPSTREAM_PORT}"


def broker_target(alias: str, path: str = ECHO_PATH, broker: str = BROKER_INTERNAL) -> str:
    """The broker proxy URL for an upstream alias + path (full URL in the path)."""
    return f"{broker.rstrip('/')}/{upstream_base(alias)}{path}"


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def variant_snapshot(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The PERF_* knobs in effect (env, then explicit overrides)."""
    snap = {k: os.environ[k] for k in VARIANT_KEYS if k in os.environ}
    snap.update(overrides or {})
    return snap


def run_metadata(scenario: str, overrides: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "git_sha": git_sha(),
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
    last: str = ""
    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(
                ["curl", "-fsS", "-o", "/dev/null", "--max-time", "2", url],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise RuntimeError("curl is required on the host") from exc
        if proc.returncode == 0:
            return
        last = proc.stderr.strip()
        time.sleep(1)
    raise TimeoutError(f"{url} not healthy after {timeout_s}s: {last}")
