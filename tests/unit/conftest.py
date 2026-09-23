"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def sqlite_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config at fresh, empty per-database SQLite files.

    Lets migration-infrastructure tests (status probe, per-migration
    up/down) run the real Alembic chains without Docker/Postgres.
    """
    cfg = tmp_path / "jentic-one.yaml"
    lines = ["databases:"]
    for name in ("admin", "control", "registry"):
        lines += [f"  {name}:", "    backend: sqlite", f"    path: {tmp_path / f'{name}.db'}"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg))
    return tmp_path


@pytest.fixture()
def sample_config_dict() -> dict[str, Any]:
    """A valid configuration as a plain dict."""
    return {
        "databases": {
            "registry": {
                "host": "db.local",
                "port": 5432,
                "name": "jentic",
                "user": "reg_user",
                "password": "reg_secret",
                "pool_max": 10,
                "schema_name": "registry",
            },
            "admin": {
                "host": "db.local",
                "port": 5432,
                "name": "jentic",
                "user": "admin_user",
                "password": "admin_secret",
                "pool_max": 5,
                "schema_name": "admin",
            },
            "control": {
                "host": "db.local",
                "port": 5432,
                "name": "jentic",
                "user": "ctrl_user",
                "password": "ctrl_secret",
                "pool_max": 8,
                "schema_name": "control",
            },
        },
        "services": {
            "request_timeout_s": 15.0,
            "retry_max": 5,
            "retry_backoff_s": 2.0,
        },
        "runtime": {
            "debug": True,
            "log_level": "DEBUG",
            "maintenance_mode": False,
        },
    }


@pytest.fixture()
def config_file(tmp_path: Path, sample_config_dict: dict[str, Any]) -> Path:
    """Write sample config to a YAML file and return its path."""
    config_path = tmp_path / "jentic-one.yaml"
    config_path.write_text(yaml.dump(sample_config_dict))
    return config_path
