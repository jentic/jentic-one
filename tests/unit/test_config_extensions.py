"""Tests for the config extension seam (register_config + AppConfig.extension).

Covers the registrable extension sub-config mechanics that let a downstream
package add top-level config sections without editing the core schema:

- ``register_config`` (registration + idempotency + collision guards),
- ``registered_config_models`` (registry snapshot),
- ``AppConfig.extension`` (typed lookup by section name),
- ``load_config`` extraction of a registered section (so ``extra="forbid"`` on
  the core model accepts it) + rejection of *unregistered* top-level keys.

Each test snapshots and restores the process-global ``_CONFIG_EXTENSIONS`` so it
never leaks a registration into another test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from jentic_one.shared import config as config_mod
from jentic_one.shared.config import (
    AppConfig,
    ConfigError,
    load_config,
    register_config,
    registered_config_models,
)


class _ExtensionModel(BaseModel):
    """A stand-in downstream extension sub-config."""

    enabled: bool = False
    label: str = "default"


class _OtherExtensionModel(BaseModel):
    threshold: int = 10


@pytest.fixture(autouse=True)
def _isolate_config_registry() -> Iterator[None]:
    """Snapshot/restore the global extension registry around each test."""
    snapshot = dict(config_mod._CONFIG_EXTENSIONS)
    try:
        yield
    finally:
        config_mod._CONFIG_EXTENSIONS.clear()
        config_mod._CONFIG_EXTENSIONS.update(snapshot)


def _minimal_config() -> dict[str, Any]:
    return {
        "databases": {
            "registry": {"name": "reg"},
            "admin": {"name": "admin"},
            "control": {"name": "ctrl"},
        }
    }


def test_register_config_adds_to_registry() -> None:
    register_config("my_ext", _ExtensionModel)
    assert registered_config_models()["my_ext"] is _ExtensionModel


def test_registered_config_models_is_a_copy() -> None:
    """The snapshot must not be the live registry (mutating it can't corrupt state)."""
    register_config("my_ext", _ExtensionModel)
    snapshot = registered_config_models()
    snapshot["injected"] = _OtherExtensionModel
    assert "injected" not in registered_config_models()


def test_register_config_is_idempotent_for_same_pair() -> None:
    register_config("my_ext", _ExtensionModel)
    register_config("my_ext", _ExtensionModel)  # no raise
    assert registered_config_models()["my_ext"] is _ExtensionModel


def test_register_config_rejects_conflicting_reregister() -> None:
    register_config("my_ext", _ExtensionModel)
    with pytest.raises(ConfigError, match="already registered"):
        register_config("my_ext", _OtherExtensionModel)


@pytest.mark.parametrize("core_field", ["broker", "search", "databases", "telemetry"])
def test_register_config_rejects_core_field_names(core_field: str) -> None:
    """An extension must not hijack a core AppConfig field."""
    assert core_field in AppConfig.model_fields
    with pytest.raises(ConfigError, match="collides with a core AppConfig field"):
        register_config(core_field, _ExtensionModel)


def test_register_config_rejects_reserved_extensions_key() -> None:
    """The reserved ``extensions`` container itself cannot be shadowed."""
    with pytest.raises(ConfigError, match="reserved 'extensions' key"):
        register_config("extensions", _ExtensionModel)


def test_appconfig_extension_returns_none_when_absent() -> None:
    cfg = AppConfig.model_validate(_minimal_config())
    assert cfg.extension("my_ext") is None


def test_load_config_extracts_and_validates_registered_section(tmp_path: Path) -> None:
    register_config("my_ext", _ExtensionModel)
    data = _minimal_config()
    data["my_ext"] = {"enabled": True, "label": "prod"}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(data))

    cfg = load_config(path)

    ext = cfg.extension("my_ext")
    assert isinstance(ext, _ExtensionModel)
    assert ext.enabled is True
    assert ext.label == "prod"


def test_load_config_invalid_extension_section_raises(tmp_path: Path) -> None:
    register_config("my_ext", _ExtensionModel)
    data = _minimal_config()
    # ``label`` is a str; a nested mapping is the wrong type and fails validation
    # inside the extension model (surfaced as a ConfigError, not a raw pydantic one).
    data["my_ext"] = {"label": {"nested": "wrong-type"}}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(data))

    with pytest.raises(ConfigError, match="Invalid config for extension 'my_ext'"):
        load_config(path)


def test_load_config_rejects_unregistered_top_level_key(tmp_path: Path) -> None:
    """A top-level key that is neither core nor registered fails loudly (extra=forbid)."""
    data = _minimal_config()
    data["totally_unknown_section"] = {"foo": "bar"}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(data))

    with pytest.raises(ConfigError, match="validation failed"):
        load_config(path)


def test_appconfig_forbids_unknown_top_level_field() -> None:
    """The core model itself rejects unknown keys (the breaking-change guard)."""
    data = _minimal_config()
    data["typo_section"] = {"x": 1}
    with pytest.raises(ValidationError, match=r"typo_section|extra"):
        AppConfig.model_validate(data)
