"""Locust scenarios for the broker perf suite (closed-loop load, saturation, soak).

Runs in the ``locustio/locust`` image (see docker-compose.perf.yaml, profile
``load``) with the repo's ``tests/`` mounted at ``/work/tests``. Reads
``results/.perf-env.json`` written by ``provision.py``. Stdlib + locust only.

Pick user classes on the command line (``locust ... BrokerEchoUser``) or in the
web UI's class picker (``--class-picker``).

Latency caveat: these users are closed loop (``constant_throughput`` pacing), so
once the broker slows down the offered load drops with it and queueing time is
never recorded (coordinated omission). Locust latencies above the knee are a
lower bound: take the throughput ceiling from ``bench_capacity`` and latency vs
load from an oha open-loop rate sweep (``bench_overhead --rates``).

Knobs (env):

* ``PERF_SHAPE`` — ``step`` | ``spike`` | ``soak`` (unset = manual users/rate)
* ``PERF_USER_RPS`` — per-user pacing via ``constant_throughput`` (default 1)
* ``PERF_CALLER`` — ``jak`` | ``at`` | ``jwt`` (default ``jak``)
* ``PERF_VENDOR`` — perf vendor slug for ``BrokerEchoUser`` (default perf-noauth)
* ``PERF_UPSTREAM_DELAY`` — seconds; forwarded as ``X-Mock-Delay`` (slow upstream)
* ``PERF_STATUS_SEQUENCE`` — e.g. ``503,503,200`` for ``RetryUser``
* ``PERF_IMPORT_SPEC`` — path (in-container) to a spec for ``ImportUser``
* ``PERF_STREAMING`` — must match the broker's ``stream_passthrough_enabled``
  (compose passes the same value to both); ``DisconnectUser`` asserts on it
* ``PERF_LOCUST_WORKERS`` — number of worker processes (``PoolAgentUser`` strides
  the agent pool by it so workers don't hand out the same agents)
* ``PERF_RUN_S`` — planned run length; tokens must outlive it (default 600)

Idempotency: any ``Idempotency-Key`` forces the broker onto the *buffered*
path, so compare ``IdempotentFirstCallUser`` / ``IdempotentReplayUser`` against a
``BrokerEchoUser`` run with ``PERF_STREAMING=false`` — not the streaming default.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

from locust import FastHttpUser, LoadTestShape, constant_throughput, events, task

from tests.perf.common import ECHO_PATH, GET_PATH, VENDORS, PerfEnv, upstream_base

ENV = PerfEnv.load()
USER_RPS = float(os.environ.get("PERF_USER_RPS", "1"))
CALLER = os.environ.get("PERF_CALLER", "jak")
_SOAK_S = float(os.environ.get("PERF_SOAK_S", "1800"))
_RUN_S = float(os.environ.get("PERF_RUN_S", "600"))
# Refuse to start on tokens that would expire mid-run (every call would 401).
# The admin token drives ImportUser / ControlReadUser.
ENV.check_fresh(
    callers=[CALLER],
    admin=True,
    run_s=max(_RUN_S, _SOAK_S if os.environ.get("PERF_SHAPE") == "soak" else 0.0),
    shorten="lower PERF_RUN_S (or PERF_SOAK_S for the soak shape)",
)
LOCUST_WORKERS = max(1, int(os.environ.get("PERF_LOCUST_WORKERS", "1")))
VENDOR = os.environ.get("PERF_VENDOR", "perf-noauth")
UPSTREAM_DELAY = os.environ.get("PERF_UPSTREAM_DELAY", "")
STATUS_SEQUENCE = os.environ.get("PERF_STATUS_SEQUENCE", "") or "503,503,200"
STREAMING = os.environ.get("PERF_STREAMING", "true").lower() == "true"
BODY = b'{"probe":true}'
JSON_HEADERS = {"Content-Type": "application/json"}
ASYNC_POLL_S = 0.2
ASYNC_TIMEOUT_S = 60.0


def _auth(token: str) -> dict[str, str]:
    return {**JSON_HEADERS, "Authorization": f"Bearer {token}"}


def _proxy_path(vendor_slug: str, path: str = ECHO_PATH) -> str:
    alias = ENV.vendors[vendor_slug]["alias"]
    return f"/{upstream_base(alias)}{path}"


def _fire(name: str, started: float, exc: Exception | None = None, length: int = 0) -> None:
    """Record a synthetic request (end-to-end timings Locust can't see natively)."""
    events.request.fire(
        request_type="E2E",
        name=name,
        response_time=(time.perf_counter() - started) * 1000,
        response_length=length,
        exception=exc,
        context={},
    )


class _BrokerUser(FastHttpUser):
    abstract = True
    host = ENV.broker_url
    wait_time = constant_throughput(USER_RPS)
    # Default 60 s connection/network timeouts; the broker's own upstream
    # timeout (30 s) should fire first.
    network_timeout = 60.0
    connection_timeout = 10.0

    def headers(self) -> dict[str, str]:
        h = _auth(ENV.caller(CALLER).token)
        if UPSTREAM_DELAY:
            h["X-Mock-Delay"] = UPSTREAM_DELAY
        return h


class BrokerEchoUser(_BrokerUser):
    """Steady small POSTs through the broker to one vendor (the baseline load)."""

    @task
    def echo(self) -> None:
        self.client.post(
            _proxy_path(VENDOR), data=BODY, headers=self.headers(), name=f"echo {VENDOR}"
        )


class MultiHostUser(_BrokerUser):
    """Spread load over the 5 extra upstream hosts (global pool vs per-host cap)."""

    hosts: ClassVar[list[str]] = [v.slug for v in VENDORS if v.slug.startswith("perf-up-")]

    @task
    def echo(self) -> None:
        slug = random.choice(self.hosts)
        self.client.post(
            _proxy_path(slug), data=BODY, headers=self.headers(), name="echo multi-host"
        )


class PoolAgentUser(_BrokerUser):
    """Each user is a different agent (rate-limit fairness / per-actor caches).

    Users on worker ``w`` of ``PERF_LOCUST_WORKERS`` take pool slots
    ``w, w + W, w + 2W, …``, so workers never hand out the same agent until the
    pool wraps.
    """

    _spawned = 0

    def on_start(self) -> None:
        pool = ENV.pool_agents or [ENV.caller("jak")]
        # A local (single-process) runner has no worker index: stride 1.
        index = getattr(self.environment.runner, "worker_index", None)
        worker, workers = (index, LOCUST_WORKERS) if index is not None and index >= 0 else (0, 1)
        slot = worker + workers * PoolAgentUser._spawned
        PoolAgentUser._spawned += 1
        self.token = pool[slot % len(pool)].token

    @task
    def echo(self) -> None:
        self.client.post(
            _proxy_path("perf-noauth"), data=BODY, headers=_auth(self.token), name="echo pool-agent"
        )


class RetryUser(_BrokerUser):
    """Upstream fails then succeeds (needs PERF_RETRY=true, PERF_BREAKER=false).

    Uses ``GET`` with an ``Idempotency-Key``: the key routes the sync call onto
    the buffered path (where ``RetryRunner.run`` executes), and ``GET`` satisfies
    the post-send status-retry gate (429/5xx after the request was sent retries
    only for idempotent HTTP methods; see ``broker/adapters/runners/retry.py``).
    """

    @task
    def flaky(self) -> None:
        h = self.headers()
        h["X-Mock-Status-Sequence"] = STATUS_SEQUENCE
        h["X-Mock-Test-Id"] = uuid.uuid4().hex
        h["Idempotency-Key"] = uuid.uuid4().hex
        self.client.get(_proxy_path("perf-noauth", GET_PATH), headers=h, name="echo retry-seq")


class FailingUpstreamUser(_BrokerUser):
    """Upstream always 503: watch the breaker open and fail fast."""

    @task
    def failing(self) -> None:
        h = self.headers()
        h["X-Mock-Status"] = "503"
        with self.client.post(
            _proxy_path("perf-noauth"),
            data=BODY,
            headers=h,
            name="echo always-503",
            catch_response=True,
        ) as resp:
            # 503 relayed or breaker fast-fail are both expected; label them.
            if resp.status_code in (502, 503, 504):
                resp.success()
            else:
                resp.failure(f"expected 502/503/504, got {resp.status_code}")


class DisconnectUser(_BrokerUser):
    """Upstream drops mid-body: streaming truncates, buffered returns an envelope.

    Buffered (``PERF_STREAMING=false``) must answer a problem+json 5xx carrying
    ``error_origin``: 500 or 502 for the transport error (depending on the
    broker version's mapping), and 503 once the breaker opens.
    Streaming has already sent a 200 status line, so the client sees 200 or a
    mid-stream protocol abort (status 0). Anything else is a regression.
    """

    @task
    def disconnect(self) -> None:
        h = self.headers()
        h["X-Mock-Disconnect"] = "true"
        with self.client.post(
            _proxy_path("perf-noauth"),
            data=BODY,
            headers=h,
            name="echo disconnect",
            catch_response=True,
        ) as resp:
            expected = (0, 200) if STREAMING else (500, 502, 503)
            enveloped = STREAMING or "error_origin" in (resp.text or "")
            if resp.status_code in expected and enveloped:
                resp.success()
            else:
                mode = "streaming" if STREAMING else "buffered"
                resp.failure(f"{mode}: expected {expected}, got {resp.status_code}")


class IdempotentFirstCallUser(_BrokerUser):
    """Unique Idempotency-Key per request: the first-call (claim + store) cost."""

    @task
    def first_call(self) -> None:
        h = self.headers()
        h["Idempotency-Key"] = uuid.uuid4().hex
        self.client.post(_proxy_path("perf-noauth"), data=BODY, headers=h, name="echo idem-first")


class IdempotentReplayUser(_BrokerUser):
    """Fixed Idempotency-Key per user: after the first call, every call is a replay."""

    def on_start(self) -> None:
        self.idem_key = uuid.uuid4().hex

    @task
    def replay(self) -> None:
        h = self.headers()
        h["Idempotency-Key"] = self.idem_key
        self.client.post(_proxy_path("perf-noauth"), data=BODY, headers=h, name="echo idem-replay")


class AsyncJobUser(_BrokerUser):
    """``Prefer: respond-async`` → 202, then poll the job to completion (E2E time)."""

    @task
    def async_call(self) -> None:
        started = time.perf_counter()
        h = self.headers()
        h["Prefer"] = "respond-async"
        with self.client.post(
            _proxy_path("perf-noauth"),
            data=BODY,
            headers=h,
            name="echo async-202",
            catch_response=True,
        ) as resp:
            if resp.status_code != 202:
                resp.failure(f"expected 202, got {resp.status_code}")
                return
            job_id = resp.json()["job_id"]
        deadline = time.monotonic() + ASYNC_TIMEOUT_S
        while time.monotonic() < deadline:
            r = self.client.get(
                f"{ENV.app_url}/jobs/{job_id}", headers=self.headers(), name="GET /jobs/{id}"
            )
            status = r.json().get("status") if r.status_code == 200 else None
            if status == "completed":
                _fire("async job completion", started)
                return
            if status in ("failed", "dead_lettered"):
                _fire("async job completion", started, RuntimeError(status))
                return
            time.sleep(ASYNC_POLL_S)
        _fire("async job completion", started, TimeoutError(job_id))


def _import_spec() -> dict[str, Any]:
    custom = os.environ.get("PERF_IMPORT_SPEC")
    if custom:
        return dict(json.loads(Path(custom).read_text()))
    # Default: the harness spec (small). Fetched lazily from the upstream.
    return {}


class ImportUser(FastHttpUser):
    """Inline spec import + job completion under load (admin/control interference)."""

    host = ENV.app_url
    wait_time = constant_throughput(float(os.environ.get("PERF_IMPORT_RPS", "0.2")))
    spec: ClassVar[dict[str, Any]] = {}

    def on_start(self) -> None:
        if not ImportUser.spec:
            ImportUser.spec = (
                _import_spec()
                or self.client.get(
                    f"{upstream_base('smoke-noauth')}/specs/live.json", name="fetch harness spec"
                ).json()
            )

    @task
    def import_spec(self) -> None:
        started = time.perf_counter()
        vendor = f"perf-imp-{uuid.uuid4().hex[:10]}"
        spec = json.loads(json.dumps(ImportUser.spec))
        # Unique host per import so the new API never collides on URL lookup.
        spec["servers"] = [{"url": f"http://{vendor}.perf.internal:8084"}]
        content = json.dumps(spec)
        source = {"type": "inline", "content": content, "filename": "spec.json", "vendor": vendor}
        with self.client.post(
            "/apis",
            json={"sources": [source]},
            headers=_auth(ENV.admin_token),
            name="POST /apis (inline)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 202:
                resp.failure(f"expected 202, got {resp.status_code}")
                return
            job_id = resp.json()["job_id"]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            r = self.client.get(
                f"/jobs/{job_id}", headers=_auth(ENV.admin_token), name="GET /jobs/{id}"
            )
            status = r.json().get("status") if r.status_code == 200 else None
            if status == "completed":
                _fire("import completion", started, length=len(content))
                return
            if status in ("failed", "dead_lettered"):
                _fire("import completion", started, RuntimeError(str(r.json().get("error"))))
                return
            time.sleep(0.5)
        _fire("import completion", started, TimeoutError(job_id))


class ControlReadUser(FastHttpUser):
    """Admin/control reads that contend with broker writes (monitoring, executions, search)."""

    host = ENV.app_url
    wait_time = constant_throughput(float(os.environ.get("PERF_CONTROL_RPS", "1")))

    @task(3)
    def executions(self) -> None:
        self.client.get(
            "/executions?limit=50", headers=_auth(ENV.admin_token), name="GET /executions"
        )

    @task(1)
    def monitoring(self) -> None:
        self.client.get(
            "/monitoring/executions?days=1",
            headers=_auth(ENV.admin_token),
            name="GET /monitoring/executions",
        )

    @task(1)
    def search(self) -> None:
        self.client.post(
            "/search",
            json={"query": "echo request", "limit": 10},
            headers=_auth(ENV.admin_token),
            name="POST /search",
        )


# ── Load shapes (opt-in via PERF_SHAPE; a defined shape overrides -u/-r) ─────

_SHAPE = os.environ.get("PERF_SHAPE", "")
_STEP_USERS = int(os.environ.get("PERF_STEP_USERS", "10"))
_STEP_S = float(os.environ.get("PERF_STEP_S", "30"))
_MAX_USERS = int(os.environ.get("PERF_MAX_USERS", "200"))


if _SHAPE == "step":

    class StepShape(LoadTestShape):
        """+PERF_STEP_USERS every PERF_STEP_S up to PERF_MAX_USERS (find the knee)."""

        def tick(self) -> tuple[int, float] | None:
            step = int(self.get_run_time() // _STEP_S) + 1
            users = step * _STEP_USERS
            if users > _MAX_USERS:
                return None
            return users, float(_STEP_USERS)

elif _SHAPE == "spike":

    class SpikeShape(LoadTestShape):
        """Baseline, 10x spike, recovery: shedding (429) and recovery time."""

        stages = (
            (60, _STEP_USERS),
            (90, _STEP_USERS * 10),
            (180, _STEP_USERS),
        )

        def tick(self) -> tuple[int, float] | None:
            t = self.get_run_time()
            for end, users in self.stages:
                if t < end:
                    return users, float(max(users, 1))
            return None

elif _SHAPE == "soak":

    class SoakShape(LoadTestShape):
        """Constant load for PERF_SOAK_S (default 30 min): leaks, pool drift, growth."""

        duration = _SOAK_S

        def tick(self) -> tuple[int, float] | None:
            if self.get_run_time() > self.duration:
                return None
            return _STEP_USERS, float(_STEP_USERS)
