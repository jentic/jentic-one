"""Background-job startup gates on the *enabled surface set*, not DB presence.

The import worker + catalog/expiry scanners are surface-owned background jobs
that draw from a **shared** jobs table. A broker-only process is granted the
registry/control DBs (``SURFACE_DB_DEPS["broker"]``) so its synchronous proxy
can resolve specs and inject credentials — but it must not run registry/control
background work, or it races (and wins) ``IMPORT`` jobs against the control
plane and runs the ingest pipeline with the wrong service's config. These tests
pin the gate on ``enabled_apps`` (from ``config.apps``), independent of
``has_db``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.shared.config import AppConfig, ConfigError
from jentic_one.shared.context import Context
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.web import app_factory

_AF = "jentic_one.shared.web.app_factory"


def _ctx(*, dbs: set[str]) -> MagicMock:
    """A Context stub whose ``has_db`` answers from an explicit DB set."""
    ctx = MagicMock()
    ctx.has_db.side_effect = lambda name: name in dbs
    return ctx


def test_import_handler_registered_only_when_registry_app_enabled() -> None:
    """Broker has the registry DB but not the registry *app* → no IMPORT handler.

    A control-plane process (registry app enabled, registry DB present) registers
    the IMPORT handler as before.
    """
    with (
        patch(f"{_AF}.WorkerLoop") as worker_loop,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}.ImportHandler") as import_handler,
    ):
        # Broker: registry DB granted for sync-proxy spec lookup, but the broker
        # surface — not registry — is enabled. IMPORT must NOT be registered, and
        # with no other handler the worker must not start at all.
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        result = app_factory._start_worker(
            broker_ctx,
            {"broker"},
            upstream_executor=None,
            credential_injector=None,
        )
        import_handler.assert_not_called()
        assert result is None
        worker_loop.assert_not_called()

    with (
        patch(f"{_AF}.WorkerLoop") as worker_loop,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}.ImportHandler") as import_handler,
    ):
        registry_ctx = _ctx(dbs={"admin", "registry"})
        result = app_factory._start_worker(registry_ctx, {"registry"})
        import_handler.assert_called_once_with(registry_ctx)
        assert result is not None
        # The registry built exactly the IMPORT handler for the worker.
        registry_arg = worker_loop.call_args.args[1]
        assert registry_arg.kinds == {JobKind.IMPORT}


def test_execution_handler_still_registered_for_broker() -> None:
    """The broker's own job (EXECUTION) is unaffected by the app gate.

    Gating IMPORT on the registry app must not disturb the broker: with its
    upstream executor present and the control DB granted, EXECUTION registers and
    the worker starts (to drain execution jobs).
    """
    with (
        patch(f"{_AF}.WorkerLoop") as worker_loop,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}.ImportHandler") as import_handler,
        patch(f"{_AF}.ExecutionHandler") as execution_handler,
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        broker_ctx.config.broker.upstream_timeout_s = 30
        injector, authorizer = MagicMock(), MagicMock()
        result = app_factory._start_worker(
            broker_ctx,
            {"broker"},
            upstream_executor=MagicMock(),
            credential_injector=injector,
            execution_authorizer=authorizer,
        )
        import_handler.assert_not_called()
        assert result is not None
        registry_arg = worker_loop.call_args.args[1]
        assert registry_arg.kinds == {JobKind.EXECUTION}
        # The run-time re-authorizer reaches the handler alongside the injector.
        kwargs = execution_handler.call_args.kwargs
        assert kwargs["credential_injector"] is injector
        assert kwargs["execution_authorizer"] is authorizer


def test_catalog_scanner_gated_on_registry_app() -> None:
    """Catalog update scanner runs for the registry surface, never for a broker."""
    with (
        patch(f"{_AF}.CatalogUpdateScanner") as scanner,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_catalog_update_scanner(broker_ctx, {"broker"}) is None
        scanner.assert_not_called()

    with (
        patch(f"{_AF}.CatalogUpdateScanner") as scanner,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        registry_ctx = _ctx(dbs={"admin", "registry"})
        assert app_factory._start_catalog_update_scanner(registry_ctx, {"registry"}) is not None
        scanner.assert_called_once_with(registry_ctx)


def test_catalog_source_scanner_needs_the_registry_app_and_db_only() -> None:
    """The source sync scanner runs with the registry surface + registry DB, no admin DB."""
    with (
        patch(f"{_AF}.CatalogSourceSyncScanner") as scanner,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_catalog_source_scanner(broker_ctx, {"broker"}) is None
        no_db = _ctx(dbs={"admin"})
        assert app_factory._start_catalog_source_scanner(no_db, {"registry"}) is None
        scanner.assert_not_called()

        standalone = _ctx(dbs={"registry"})
        assert app_factory._start_catalog_source_scanner(standalone, {"registry"}) is not None
        scanner.assert_called_once_with(standalone)


def test_catalog_sources_are_validated_for_the_registry_app_and_db_only() -> None:
    with patch(f"{_AF}.validate_catalog_sources") as validate:
        app_factory._validate_catalog_sources(_ctx(dbs={"registry"}), {"broker"})
        app_factory._validate_catalog_sources(_ctx(dbs={"admin"}), {"registry"})
        validate.assert_not_called()
        standalone = _ctx(dbs={"registry"})
        app_factory._validate_catalog_sources(standalone, {"registry"})
        validate.assert_called_once_with(standalone)


@pytest.mark.parametrize("combined", [False, True])
async def test_invalid_catalog_sources_fail_startup_before_any_background_task(
    combined: bool, sample_config_dict: dict[str, Any]
) -> None:
    """A ``ConfigError`` from ``catalog.sources`` is raised before the worker or any
    scanner starts, so a failed boot leaves no task running."""
    ctx = Context(AppConfig.model_validate(sample_config_dict))
    started: list[str] = []

    def _record(name: str) -> Any:
        return lambda *a, **k: started.append(name)

    with (
        patch.object(Context, "startup", AsyncMock()),
        patch.object(Context, "shutdown", AsyncMock()),
        patch(f"{_AF}._validate_catalog_sources", side_effect=ConfigError("bad sources")),
        patch(f"{_AF}._start_telemetry", AsyncMock(return_value=None)),
        patch(f"{_AF}.instrument_databases"),
        patch(f"{_AF}._start_worker", side_effect=_record("worker")),
        patch(f"{_AF}._start_expiry_scanner", side_effect=_record("expiry")),
        patch(f"{_AF}._start_catalog_update_scanner", side_effect=_record("update")),
        patch(f"{_AF}._start_catalog_source_scanner", side_effect=_record("sources")),
    ):
        if combined:
            app = app_factory.create_combined_app(ctx, ["registry"])
        else:
            app = app_factory.create_surface_app(
                ctx, title="registry", routers=[], enabled_apps={"registry"}
            )
        with pytest.raises(ConfigError):
            async with app.router.lifespan_context(app):
                pass
    assert started == []


def test_expiry_scanner_gated_on_control_app() -> None:
    """Credential-expiry scanner runs for the control surface, never for a broker.

    The broker is granted the control DB to resolve credentials, but the expiry
    sweep is a control-plane job.
    """
    with (
        patch(f"{_AF}.CredentialExpiryScanner") as scanner,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_expiry_scanner(broker_ctx, {"broker"}) is None
        scanner.assert_not_called()

    with (
        patch(f"{_AF}.CredentialExpiryScanner") as scanner,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        control_ctx = _ctx(dbs={"admin", "control"})
        assert app_factory._start_expiry_scanner(control_ctx, {"control"}) is not None
        scanner.assert_called_once()


def test_combined_app_still_runs_all_background_jobs() -> None:
    """Dev-combined (all surfaces in one process) keeps every background job.

    The gate is per-enabled-app, not global: when registry + control are both in
    the enabled set, IMPORT registers and both scanners start.
    """
    combined = {"registry", "admin", "control", "auth"}
    with (
        patch(f"{_AF}.WorkerLoop") as worker_loop,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}.ImportHandler") as import_handler,
    ):
        ctx = _ctx(dbs={"admin", "registry", "control"})
        assert app_factory._start_worker(ctx, combined) is not None
        import_handler.assert_called_once_with(ctx)
        assert worker_loop.call_args.args[1].kinds == {JobKind.IMPORT}

    with (
        patch(f"{_AF}.CatalogUpdateScanner"),
        patch(f"{_AF}.CredentialExpiryScanner"),
        patch(f"{_AF}.asyncio.create_task"),
    ):
        ctx = _ctx(dbs={"admin", "registry", "control"})
        assert app_factory._start_catalog_update_scanner(ctx, combined) is not None
        assert app_factory._start_expiry_scanner(ctx, combined) is not None
