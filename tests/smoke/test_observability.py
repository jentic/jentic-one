"""Observability smoke tests — verify logs, metrics, and traces flow end-to-end.

These tests are only meaningful when the LGTM observability stack is deployed
alongside the app (e.g. `make obs-up && make deploy-local OTEL=1`, or the
`combined+obs` cell of the helm smoke matrix). They auto-skip when:

  * The `OBS` environment variable is not set to a truthy value.
  * `kubectl` isn't on the path or can't reach the cluster.
  * The `monitoring` namespace doesn't exist (obs stack not deployed).

The tests work by `kubectl port-forward`-ing the Prometheus / Loki / Tempo
services into the test process and querying their HTTP APIs. We deliberately
do *not* go through Grafana — Grafana adds an unrelated failure surface
(datasource config, auth) and makes assertions noisier. The query APIs are
the actual source of truth.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

MONITORING_NS = os.environ.get("MONITORING_NS", "monitoring")
OBS_RELEASE = os.environ.get("OBS_RELEASE", "obs")

# Service identity used across all three telemetry backends. Set by the
# common Helm template at
# `deploy/helm/jentic-one/charts/common/templates/_logging.tpl` as
# `<release>-<chart>` and propagated:
#   - via OTEL_SERVICE_NAME → OTLP resource attr `service.name` → Tempo,
#     and (after `resource_to_telemetry_conversion`) → Prometheus label
#     `service_name`.
#   - via Alloy's pod-label relabel rule → Loki stream label `service_name`.
TARGET_SERVICE = os.environ.get("OBS_TEST_SERVICE", "jentic-app")

# How long to wait for telemetry to land in each backend after generating
# traffic. The OTel SDK in the app uses a 15s metric export interval by
# default (see MetricsConfig.export_interval_seconds), Loki/Alloy is
# near-realtime, and Tempo flushes spans on shutdown of the batch — so the
# binding constraint is metrics. Use 60s as a generous upper bound that's
# still tight enough to fail fast when telemetry is genuinely broken.
SETTLE_SECONDS = int(os.environ.get("OBS_TEST_SETTLE_SECONDS", "60"))
POLL_INTERVAL = 2.0


def _truthy(val: str | None) -> bool:
    return val is not None and val.lower() in ("1", "true", "yes", "on")


def _kubectl_available() -> bool:
    try:
        subprocess.run(
            ["kubectl", "version", "--client=true", "--output=json"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _ns_exists(namespace: str) -> bool:
    result = subprocess.run(
        ["kubectl", "get", "ns", namespace, "-o", "name"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def _port_forward(service: str, remote_port: int) -> Iterator[int]:
    """Run `kubectl port-forward` for the lifetime of the context.

    Picks a free local port to avoid collisions when tests run in parallel
    or alongside `make grafana`. Yields the local port once the forward is
    actually accepting connections.
    """
    local_port = _free_port()
    proc = subprocess.Popen(
        [
            "kubectl",
            "-n",
            MONITORING_NS,
            "port-forward",
            f"svc/{service}",
            f"{local_port}:{remote_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if proc.poll() is not None:
                raise RuntimeError(f"port-forward to svc/{service} exited early")
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"port-forward to svc/{service} never became ready")
        yield local_port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _http_get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            return data
    except (HTTPError, URLError) as exc:
        raise AssertionError(f"GET {url} failed: {exc}") from exc


def _generate_traffic(base_url: str, count: int = 30) -> None:
    """Hit /health a few times so there's actually telemetry to check.

    The combined-mode pod responds to /health at root (and per-surface
    /<surface>/health for the parts mode mounted under combined). One path
    is enough — we're testing the pipeline, not request fanout.
    """
    for _ in range(count):
        try:
            with urlopen(f"{base_url}/health", timeout=2) as resp:
                resp.read()
        except (HTTPError, URLError):
            # A single failed request shouldn't fail the obs test — we just
            # need *some* requests to land for telemetry to exist.
            continue


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def obs_enabled() -> None:
    """Skip the entire module if the obs stack isn't expected to be there."""
    if not _truthy(os.environ.get("OBS")):
        pytest.skip("OBS env var not set; observability stack not under test")
    if not _kubectl_available():
        pytest.skip("kubectl not available")
    if not _ns_exists(MONITORING_NS):
        pytest.skip(f"namespace {MONITORING_NS!r} not found; obs stack not deployed")


@pytest.fixture(scope="module")
def traffic_generated(obs_enabled: None, base_url: str) -> None:
    """Send a small burst of traffic before assertions so telemetry exists."""
    _generate_traffic(base_url)
    # Give the app's PeriodicExportingMetricReader at least one export
    # interval to flush; logs/traces are near-realtime so this also covers
    # them. Polling inside each test still applies for tighter feedback.
    time.sleep(2)


# --- Tests ------------------------------------------------------------------


@pytest.mark.smoke
def test_metrics_reach_prometheus(traffic_generated: None) -> None:
    """Prometheus has app metrics labeled with our service_name."""
    query = f'http_server_duration_milliseconds_count{{service_name="{TARGET_SERVICE}"}}'
    deadline = time.monotonic() + SETTLE_SECONDS
    last_payload: dict[str, Any] | None = None

    with _port_forward(f"{OBS_RELEASE}-prometheus-server", 80) as port:
        url = f"http://127.0.0.1:{port}/api/v1/query?{urlencode({'query': query})}"
        while time.monotonic() < deadline:
            payload = _http_get_json(url)
            last_payload = payload
            assert payload.get("status") == "success", f"prometheus error: {payload}"
            result = payload.get("data", {}).get("result", [])
            if result:
                # Sanity: at least one series, and the value is parseable.
                series = result[0]
                assert "metric" in series and "value" in series
                assert series["metric"].get("service_name") == TARGET_SERVICE
                return
            time.sleep(POLL_INTERVAL)

    pytest.fail(f"No samples for {query} after {SETTLE_SECONDS}s. Last response: {last_payload}")


@pytest.mark.smoke
def test_logs_reach_loki(traffic_generated: None) -> None:
    """Loki has app logs labeled with our service identity."""
    end_ns = int(time.time() * 1_000_000_000)
    start_ns = end_ns - 10 * 60 * 1_000_000_000  # 10m back, generous
    query = f'{{service_name="{TARGET_SERVICE}"}}'
    deadline = time.monotonic() + SETTLE_SECONDS
    last_payload: dict[str, Any] | None = None

    with _port_forward(f"{OBS_RELEASE}-loki", 3100) as port:
        params = urlencode(
            {"query": query, "start": str(start_ns), "end": str(end_ns), "limit": "5"}
        )
        url = f"http://127.0.0.1:{port}/loki/api/v1/query_range?{params}"
        while time.monotonic() < deadline:
            payload = _http_get_json(url)
            last_payload = payload
            assert payload.get("status") == "success", f"loki error: {payload}"
            streams = payload.get("data", {}).get("result", [])
            entry_count = sum(len(s.get("values", [])) for s in streams)
            if entry_count > 0:
                return
            time.sleep(POLL_INTERVAL)

    pytest.fail(
        f"No log entries for {query} after {SETTLE_SECONDS}s. Last response: {last_payload}"
    )


@pytest.mark.smoke
def test_traces_reach_tempo(traffic_generated: None) -> None:
    """Tempo has spans tagged with our service.name."""
    end_s = int(time.time())
    start_s = end_s - 10 * 60
    deadline = time.monotonic() + SETTLE_SECONDS
    last_payload: dict[str, Any] | None = None

    with _port_forward(f"{OBS_RELEASE}-tempo", 3200) as port:
        params = urlencode(
            {
                "tags": f"service.name={TARGET_SERVICE}",
                "start": str(start_s),
                "end": str(end_s),
                "limit": "5",
            }
        )
        url = f"http://127.0.0.1:{port}/api/search?{params}"
        while time.monotonic() < deadline:
            payload = _http_get_json(url)
            last_payload = payload
            traces = payload.get("traces") or []
            if traces:
                # Sanity: the search result references our service.
                assert any(
                    t.get("rootServiceName") == TARGET_SERVICE
                    or TARGET_SERVICE in (t.get("serviceStats") or {})
                    for t in traces
                ), f"traces returned but none for {TARGET_SERVICE}: {traces}"
                return
            time.sleep(POLL_INTERVAL)

    pytest.fail(
        f"No traces for service.name={TARGET_SERVICE} after {SETTLE_SECONDS}s. "
        f"Last response: {last_payload}"
    )
