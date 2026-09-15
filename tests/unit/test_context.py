"""Tests for the Context class.

Context lifecycle tests that require real database connections live in
tests/integration/test_db_connectivity.py and tests/integration/test_context_lifecycle.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from jentic_one.shared.config import AppConfig, ConfigError
from jentic_one.shared.context import Context
from jentic_one.shared.db import DatabaseSession


@pytest.fixture()
def app_config(sample_config_dict: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(sample_config_dict)


def test_creates_from_config(app_config: AppConfig) -> None:
    ctx = Context(app_config)
    assert ctx.config is app_config


def test_db_properties_are_lazy(app_config: AppConfig) -> None:
    ctx = Context(app_config)
    assert ctx._registry_db is None
    assert ctx._admin_db is None
    assert ctx._control_db is None


def test_db_properties_return_database_session_on_access(app_config: AppConfig) -> None:
    ctx = Context(app_config)
    assert isinstance(ctx.registry_db, DatabaseSession)
    assert isinstance(ctx.admin_db, DatabaseSession)
    assert isinstance(ctx.control_db, DatabaseSession)


def test_db_property_returns_same_instance(app_config: AppConfig) -> None:
    ctx = Context(app_config)
    first = ctx.registry_db
    second = ctx.registry_db
    assert first is second


def test_allowed_dbs_restricts_access(app_config: AppConfig) -> None:
    ctx = Context(app_config, allowed_dbs={"registry", "admin"})
    assert isinstance(ctx.registry_db, DatabaseSession)
    assert isinstance(ctx.admin_db, DatabaseSession)
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.control_db


def test_disallowed_db_raises_descriptive_error(app_config: AppConfig) -> None:
    ctx = Context(app_config, allowed_dbs={"control"})
    with pytest.raises(RuntimeError, match=r"'registry'.*not allowed"):
        _ = ctx.registry_db
    with pytest.raises(RuntimeError, match=r"'admin'.*not allowed"):
        _ = ctx.admin_db


def test_none_allowed_dbs_allows_all(app_config: AppConfig) -> None:
    ctx = Context(app_config, allowed_dbs=None)
    assert isinstance(ctx.registry_db, DatabaseSession)
    assert isinstance(ctx.admin_db, DatabaseSession)
    assert isinstance(ctx.control_db, DatabaseSession)


def test_empty_set_blocks_all(app_config: AppConfig) -> None:
    ctx = Context(app_config, allowed_dbs=set())
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.registry_db
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.admin_db
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.control_db


def test_single_db_allowed(app_config: AppConfig) -> None:
    ctx = Context(app_config, allowed_dbs={"admin"})
    assert isinstance(ctx.admin_db, DatabaseSession)
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.registry_db
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.control_db


async def test_startup_fails_on_invalid_configured_keyset(
    sample_config_dict: dict[str, Any],
) -> None:
    """A configured-but-invalid keyset must fail at boot, not at first
    credential use — otherwise a keyset disaster surfaces as scattered
    per-credential errors instead of one loud startup failure."""
    sample_config_dict["credentials"] = {
        "encryption": {
            "active_id": "v1",
            # Valid base64, wrong length: passes config validation for inline
            # material, must be caught by the eager startup check.
            "entries": [{"id": "v1", "material": "c2hvcnQ="}],
        }
    }
    config = AppConfig.model_validate(sample_config_dict)
    ctx = Context(config, allowed_dbs=set())
    with pytest.raises(ConfigError, match="must be 32 bytes"):
        await ctx.startup()


async def test_startup_without_keyset_boots(app_config: AppConfig) -> None:
    """A keyset-less config still boots; the ConfigError stays lazy at first
    credential use (deploys that never write credentials keep working)."""
    ctx = Context(app_config, allowed_dbs=set())
    await ctx.startup()
    try:
        with pytest.raises(ConfigError, match="must not be empty"):
            _ = ctx.encryption
    finally:
        await ctx.shutdown()
