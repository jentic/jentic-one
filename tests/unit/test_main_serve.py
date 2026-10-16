"""Tests for ``python -m jentic_one`` serving — the uvicorn call ``_serve`` builds."""

from __future__ import annotations

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


@pytest.fixture()
def serve_env(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Point ``_serve`` at the sample config and capture its ``uvicorn.run`` call.

    Observability setup and app assembly are stubbed: these tests assert which
    uvicorn call ``_serve`` makes, not what the app it serves contains.
    """
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("JENTIC__SERVER__HOST", "127.0.0.1")
    monkeypatch.setenv("JENTIC__SERVER__PORT", "8123")
    for key, value in _SHARED_SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "configure_logging", MagicMock())
    monkeypatch.setattr(cli, "configure_tracing", MagicMock())
    monkeypatch.setattr(cli, "configure_metrics", MagicMock())
    run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", run)
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

    args, kwargs = _single_call(serve_env)
    assert args == ("jentic_one.__main__:create_app",)
    assert kwargs == {"host": "127.0.0.1", "port": 8123, "factory": True, "workers": 4}
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


def test_serve_reload_runs_single_factory_worker(serve_env: MagicMock) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("JENTIC__SERVER__RELOAD", "true")
        cli._serve()

    args, kwargs = _single_call(serve_env)
    assert args == ("jentic_one.__main__:create_app",)
    assert kwargs == {"host": "127.0.0.1", "port": 8123, "reload": True, "factory": True}


def test_serve_multi_worker_warns_about_per_process_state(
    serve_env: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENTIC__SERVER__WORKERS", "2")
    monkeypatch.setenv("JENTIC__OBSERVABILITY__METRICS__EXPORTER", "prometheus")

    with structlog.testing.capture_logs() as logs:
        cli._serve()

    events = {entry["event"] for entry in logs}
    assert {"prometheus_metrics_per_worker", "shared_state_backend_memory_per_worker"} <= events


def test_metrics_resource_carries_a_per_process_instance_id() -> None:
    """Worker series stay distinct at an OTLP collector without extra wiring.

    The OpenTelemetry SDK stamps every ``Resource`` with a ``service.instance.id``
    that is unique per process, which is what keeps the cumulative series of
    several ``server.workers`` processes from colliding.
    """
    resource = Resource.create({"service.name": "jentic-one"})
    assert resource.attributes.get("service.instance.id")
