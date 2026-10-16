"""Tests for ``python -m jentic_one`` serving — the uvicorn call ``_serve`` builds."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog.testing
import uvicorn
from fastapi import FastAPI
from opentelemetry.sdk.resources import Resource

from jentic_one import __main__ as cli
from jentic_one.shared.config import ConfigError

_SHARED_SECRETS = {
    key: f"serve-test-{key.lower()}-0123456789abcdef"
    for key in (
        "JENTIC__ADMIN__AUTH__JWT_SECRET",
        "JENTIC__ADMIN__INVITE__PEPPER",
        "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET",
    )
}

# Several workers need the shared-state backend on redis while the auth
# surface (in the default apps) runs; nothing connects to it in these tests.
_REDIS_STATE = {
    "JENTIC__BROKER__RESILIENCE__BACKEND__BACKEND": "redis",
    "JENTIC__BROKER__RESILIENCE__BACKEND__REDIS_URL": "redis://redis.local:6379/0",
}


@pytest.fixture()
def serve_env(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Point ``_serve`` at the sample config and capture its ``uvicorn.run`` call.

    The multi-worker launcher (``run_workers``) is captured too, as
    ``run.workers``.

    Observability setup and app assembly are stubbed: these tests assert which
    uvicorn call ``_serve`` makes, not what the app it serves contains.
    """
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("JENTIC__SERVER__HOST", "127.0.0.1")
    monkeypatch.setenv("JENTIC__SERVER__PORT", "8123")
    for key, value in {**_SHARED_SECRETS, **_REDIS_STATE}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "configure_logging", MagicMock())
    monkeypatch.setattr(cli, "configure_tracing", MagicMock())
    monkeypatch.setattr(cli, "configure_metrics", MagicMock())
    run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", run)
    run.workers = MagicMock()
    monkeypatch.setattr(cli, "run_workers", run.workers)
    return run


def _single_call(run: MagicMock) -> tuple[tuple[Any, ...], dict[str, Any]]:
    run.assert_called_once()
    args, kwargs = run.call_args
    return args, kwargs


def test_serve_single_worker_runs_app_instance(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    build_app = MagicMock(return_value=app)
    monkeypatch.setattr(cli, "_build_app", build_app)

    cli._serve()

    args, kwargs = _single_call(serve_env)
    assert args == (app,)
    assert kwargs == {"host": "127.0.0.1", "port": 8123}
    build_app.assert_called_once()


def test_serve_multi_worker_runs_factory_import_string(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENTIC__SERVER__WORKERS", "4")
    build_app = MagicMock()
    monkeypatch.setattr(cli, "_build_app", build_app)

    cli._serve()

    serve_env.assert_not_called()
    args, kwargs = _single_call(serve_env.workers)
    assert args == ("jentic_one.__main__:create_app",)
    assert kwargs == {"host": "127.0.0.1", "port": 8123, "workers": 4}
    # The supervisor builds no app and sets up no exporters: each worker does
    # that itself in ``create_app``.
    build_app.assert_not_called()
    cli.configure_metrics.assert_not_called()  # type: ignore[attr-defined]
    cli.configure_tracing.assert_not_called()  # type: ignore[attr-defined]


def test_serve_factory_import_string_resolves_to_create_app() -> None:
    module_name, _, attr = "jentic_one.__main__:create_app".partition(":")
    assert module_name == cli.__name__
    assert getattr(cli, attr) is cli.create_app


def test_serve_multi_worker_with_oneshot_config_source_refused(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENTIC__SERVER__WORKERS", "2")
    monkeypatch.setattr(cli, "oneshot_config_source_active", lambda: True)

    with pytest.raises(ConfigError, match="one-shot source"):
        cli._serve()

    serve_env.assert_not_called()
    serve_env.workers.assert_not_called()


def test_serve_reload_runs_single_factory_worker(serve_env: MagicMock) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("JENTIC__SERVER__RELOAD", "true")
        cli._serve()

    args, kwargs = _single_call(serve_env)
    assert args == ("jentic_one.__main__:create_app",)
    assert kwargs == {"host": "127.0.0.1", "port": 8123, "reload": True, "factory": True}


def _events(logs: list[MutableMapping[str, Any]]) -> set[str]:
    return {entry["event"] for entry in logs}


def test_serve_multi_worker_warns_about_prometheus_per_worker(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENTIC__SERVER__WORKERS", "2")
    monkeypatch.setenv("JENTIC__OBSERVABILITY__METRICS__EXPORTER", "prometheus")

    with structlog.testing.capture_logs() as logs:
        cli._serve()

    assert "prometheus_metrics_per_worker" in _events(logs)


def test_serve_multi_worker_combined_warns_about_provider_refresh(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENTIC__SERVER__WORKERS", "2")

    with structlog.testing.capture_logs() as logs:
        cli._serve()

    events = _events(logs)
    assert "provider_config_refresh_per_worker" in events
    assert "prometheus_metrics_per_worker" not in events


def test_serve_multi_worker_broker_only_emits_no_per_worker_warnings(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A redis-backed broker on the otlp exporter has nothing left that is per worker."""
    monkeypatch.setenv("JENTIC__SERVER__WORKERS", "2")
    monkeypatch.setenv("JENTIC__APPS", "broker")
    monkeypatch.setenv("JENTIC__OBSERVABILITY__METRICS__EXPORTER", "otlp")

    with structlog.testing.capture_logs() as logs:
        cli._serve()

    serve_env.workers.assert_called_once()
    warnings = {entry["event"] for entry in logs if entry.get("log_level") == "warning"}
    assert not warnings & {
        "prometheus_metrics_per_worker",
        "provider_config_refresh_per_worker",
    }


def test_metrics_resource_instance_id_differs_between_processes() -> None:
    """Worker series stay distinct at an OTLP collector without extra wiring.

    The OpenTelemetry SDK stamps every ``Resource`` with a ``service.instance.id``
    unique to the process, which keeps the cumulative series of several
    ``server.workers`` processes from colliding.
    """
    script = (
        "from opentelemetry.sdk.resources import Resource\n"
        "print(Resource.create({'service.name': 'jentic-one'})"
        ".attributes['service.instance.id'])\n"
    )
    ids = {
        subprocess.run(
            [sys.executable, "-c", script], check=True, capture_output=True, text=True
        ).stdout.strip()
        for _ in range(2)
    }
    here = Resource.create({"service.name": "jentic-one"}).attributes["service.instance.id"]
    assert len(ids | {here}) == 3
