"""Upgrade tolerance for retired config keys (theme 7).

Theme 7 removed the access-request subsystem and with it the
``control.access_requests`` config section. Installs upgrading in place still
have the section in their YAML; ``ControlSurfaceConfig`` keeps pydantic's
default ``extra="ignore"`` so those files keep loading — the retired knobs are
silently dropped, never a startup crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jentic_one.shared.config import load_config


def _minimal_config() -> dict[str, Any]:
    return {
        "databases": {
            "registry": {"name": "reg"},
            "admin": {"name": "admin"},
            "control": {"name": "ctrl"},
        }
    }


def test_yaml_with_retired_access_requests_section_still_loads(tmp_path: Path) -> None:
    """A pre-theme-7 YAML carrying ``control.access_requests.*`` keeps loading."""
    data = _minimal_config()
    data["control"] = {
        "access_requests": {
            "ttl_days": 14,
            "canonical_base_url": "https://jentic.example.com",
        }
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(data))

    cfg = load_config(path)  # must not raise

    # The retired section is dropped, not resurfaced under another name.
    assert not hasattr(cfg.control, "access_requests")
