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

from unittest.mock import MagicMock, patch

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
        patch(f"{_AF}.ExecutionHandler"),
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        broker_ctx.config.broker.upstream_timeout_s = 30
        result = app_factory._start_worker(
            broker_ctx,
            {"broker"},
            upstream_executor=MagicMock(),
            credential_injector=MagicMock(),
        )
        import_handler.assert_not_called()
        assert result is not None
        registry_arg = worker_loop.call_args.args[1]
        assert registry_arg.kinds == {JobKind.EXECUTION}


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


def test_webhook_dispatcher_gated_on_admin_app() -> None:
    """The dispatcher runs only for the admin surface, never for a co-tenant.

    The admin DB is granted to auth/broker/control/registry too
    (``SURFACE_DB_DEPS``); gating on ``has_db("admin")`` alone would spin up a
    dispatcher on ~4 processes all racing the same queue. The gate is the owning
    surface (``"admin" in enabled_apps``).
    """
    with (
        patch(f"{_AF}.WebhookDeliveryDispatcher") as dispatcher,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}._build_secret_resolver"),
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_webhook_dispatcher(broker_ctx, {"broker"}) is None
        dispatcher.assert_not_called()

    with (
        patch(f"{_AF}.WebhookDeliveryDispatcher") as dispatcher,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}._build_secret_resolver"),
    ):
        admin_ctx = _ctx(dbs={"admin"})
        assert app_factory._start_webhook_dispatcher(admin_ctx, {"admin"}) is not None
        dispatcher.assert_called_once()


def test_webhook_relay_gated_on_admin_app() -> None:
    """The relay likewise runs only for the admin surface.

    Running one relay per surface that happens to hold the admin DB would fan the
    same event out several times.
    """
    with (
        patch(f"{_AF}.InternalEventRelay") as relay,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        broker_ctx = _ctx(dbs={"admin", "control", "registry"})
        assert app_factory._start_webhook_relay(broker_ctx, {"broker"}) is None
        relay.assert_not_called()

    with (
        patch(f"{_AF}.InternalEventRelay") as relay,
        patch(f"{_AF}.asyncio.create_task"),
    ):
        admin_ctx = _ctx(dbs={"admin"})
        assert app_factory._start_webhook_relay(admin_ctx, {"admin"}) is not None
        relay.assert_called_once()


def test_webhook_dispatcher_needs_admin_db_even_when_owning_surface() -> None:
    """Owning the surface is necessary but not sufficient — the DB must exist too."""
    with (
        patch(f"{_AF}.WebhookDeliveryDispatcher") as dispatcher,
        patch(f"{_AF}.asyncio.create_task"),
        patch(f"{_AF}._build_secret_resolver"),
    ):
        no_db_ctx = _ctx(dbs=set())
        assert app_factory._start_webhook_dispatcher(no_db_ctx, {"admin"}) is None
        dispatcher.assert_not_called()
