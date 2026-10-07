"""Unit tests for the entitlement installer + lifespan (``installer.py``).

Exercises the wiring without an app factory: the installer mounts the gate,
the lifespan's startup check applies the verdict before the app serves, the
refresher flips lockout and recovery, and teardown cancels cleanly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.integrations.aws_marketplace.client import LicenseVerdict
from jentic_one.integrations.aws_marketplace.gate import EntitlementGate
from jentic_one.integrations.aws_marketplace.installer import (
    entitlement_lifespan,
    install_entitlement_gate,
)
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context


class _StubChecker:
    """Duck-typed EntitlementChecker returning a settable verdict."""

    def __init__(self, verdict: LicenseVerdict) -> None:
        self.verdict = verdict
        self.calls = 0
        self.checked = asyncio.Event()

    async def current_verdict(self) -> LicenseVerdict:
        self.calls += 1
        self.checked.set()
        return self.verdict


def _ctx(sample_config_dict: dict[str, Any], **entitlement: Any) -> Context:
    cfg = dict(sample_config_dict)
    cfg["entitlement"] = {
        "enabled": True,
        "product_code": "prod-abc",
        # Default pricing model is contract, which requires the product ID.
        "license_sku": "prod-id-abc",
        # Fast refresher cadence so the flip tests finish in milliseconds.
        "refresh_interval_seconds": 0,
        **entitlement,
    }
    config = AppConfig.model_validate(cfg)
    return cast(Context, SimpleNamespace(config=config))


def _installed_app(checker: _StubChecker, ctx: Context) -> FastAPI:
    app = FastAPI()

    @app.get("/thing")
    async def thing() -> dict[str, str]:
        return {"ok": "yes"}

    install_entitlement_gate(app, ctx)
    app.state.entitlement_checker = checker  # the lifespan's test seam
    return app


@pytest.mark.asyncio
async def test_startup_not_entitled_locks_before_first_request(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict)
    checker = _StubChecker(LicenseVerdict.NOT_ENTITLED)
    app = _installed_app(checker, ctx)

    async with entitlement_lifespan(app, ctx):
        gate: EntitlementGate = app.state.entitlement_gate
        assert gate.locked_out
        assert checker.calls >= 1


@pytest.mark.asyncio
async def test_startup_entitled_stays_open(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict)
    checker = _StubChecker(LicenseVerdict.ENTITLED)
    app = _installed_app(checker, ctx)

    async with entitlement_lifespan(app, ctx):
        assert not app.state.entitlement_gate.locked_out


@pytest.mark.asyncio
async def test_refresher_flips_lockout_and_recovery(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict)
    checker = _StubChecker(LicenseVerdict.ENTITLED)
    app = _installed_app(checker, ctx)

    async with entitlement_lifespan(app, ctx):
        gate: EntitlementGate = app.state.entitlement_gate
        assert not gate.locked_out

        checker.verdict = LicenseVerdict.NOT_ENTITLED
        await _wait_for(lambda: gate.locked_out)

        checker.verdict = LicenseVerdict.ENTITLED
        await _wait_for(lambda: not gate.locked_out)


@pytest.mark.asyncio
async def test_teardown_cancels_refresher(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict)
    checker = _StubChecker(LicenseVerdict.ENTITLED)
    app = _installed_app(checker, ctx)

    async with entitlement_lifespan(app, ctx):
        pass
    calls_after_exit = checker.calls
    await asyncio.sleep(0.05)

    assert checker.calls == calls_after_exit  # the loop really stopped


def test_full_stack_through_testclient(sample_config_dict: dict[str, Any]) -> None:
    """Startup check runs inside the app lifespan; a not-entitled app serves 503."""
    ctx = _ctx(sample_config_dict)
    checker = _StubChecker(LicenseVerdict.NOT_ENTITLED)
    app = _installed_app(checker, ctx)
    app.router.lifespan_context = lambda app_: entitlement_lifespan(app_, ctx)

    with TestClient(app) as client:
        assert client.get("/thing").status_code == 503
        assert client.get("/health").status_code == 200


async def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)
