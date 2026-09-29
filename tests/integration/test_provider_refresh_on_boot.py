"""The boot-time provider refresh runs only in processes that resolve providers.

A standalone registry (or admin/auth) process never resolves a credential
provider, and in the Helm chart's split layout the registry is not handed the
credential keyset either. Refreshing there would try to decrypt the stored
provider client secrets and log ``provider_refresh_on_boot_failed`` on every
boot; control and the broker, which do resolve providers, must still refresh.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import structlog
from sqlalchemy import text

from jentic_one.__main__ import _build_context
from jentic_one.admin.services.provider_config_service import ProviderConfigService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, EncryptionConfig
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture()
async def _encrypted_provider_config(
    integration_context: Context,
) -> AsyncGenerator[None, None]:
    """One stored provider config whose client secret is ciphertext at rest."""

    async def _wipe() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(text("DELETE FROM provider_configs"))

    await _wipe()
    await ProviderConfigService(integration_context).set(
        "pipedream",
        {
            "project_id": "proj_test",
            "client_id": "client_test",
            "environment": "production",
            "client_secret": "pd-client-secret-value",  # pragma: allowlist secret
        },
        identity=Identity(sub="usr_test_operator", email="operator@test.local"),
    )
    yield
    await _wipe()


def _without_keyset(config: AppConfig) -> AppConfig:
    """The config a pod gets when it is not handed the credential keyset."""
    credentials = config.credentials.model_copy(update={"encryption": EncryptionConfig()})
    return config.model_copy(update={"credentials": credentials})


async def _boot_logs(config: AppConfig, apps: list[str]) -> list[str]:
    ctx = _build_context(config, apps)
    with structlog.testing.capture_logs() as logs:
        await ctx.startup()
    await ctx.shutdown()
    return [entry["event"] for entry in logs]


@pytest.mark.parametrize("apps", [["registry"], ["admin", "auth"], ["auth"]])
async def test_surfaces_without_providers_skip_the_boot_refresh(
    integration_config: AppConfig, _encrypted_provider_config: None, apps: list[str]
) -> None:
    events = await _boot_logs(_without_keyset(integration_config), apps)
    assert "provider_refresh_on_boot_failed" not in events


@pytest.mark.parametrize(
    "apps", [["control"], ["broker"], ["registry", "admin", "control", "auth"]]
)
async def test_provider_surfaces_still_refresh_on_boot(
    integration_config: AppConfig, _encrypted_provider_config: None, apps: list[str]
) -> None:
    """Control, the broker and the combined app still pick up DB provider configs.

    With the keyset they resolve the stored provider; without it the refresh
    is attempted (and reports the failure), proving it was not skipped.
    """
    ctx = _build_context(integration_config, apps)
    await ctx.startup()
    try:
        assert "pipedream" in ctx.providers.list_all()
    finally:
        await ctx.shutdown()
    events = await _boot_logs(_without_keyset(integration_config), apps)
    assert "provider_refresh_on_boot_failed" in events
