"""Folding of the deprecated ``broker.resilience.backend`` section into ``state``."""

from __future__ import annotations

import threading
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from jentic_one.shared.config._common import _deep_merge
from jentic_one.shared.state.factory import StateBackendConfig

_logger = structlog.get_logger(__name__)


_DEPRECATED_STATE_PATH = "broker.resilience.backend"
_deprecated_state_section_warned = threading.Event()


def _fold_deprecated_state_section(data: Any) -> Any:
    """Read ``broker.resilience.backend`` into ``state`` for one deprecation release.

    The old section and the new one are merged leaf by leaf, so a config may
    set some keys under either. A key set under both must agree (after type
    coercion, so the env var string ``"500"`` agrees with a YAML ``500``);
    when they disagree the config fails to load, naming both paths, rather
    than silently picking a shared-state backend the operator did not mean.
    """
    if not isinstance(data, dict):
        return data
    broker = data.get("broker")
    if not isinstance(broker, dict):
        return data
    resilience = broker.get("resilience")
    if not isinstance(resilience, dict) or "backend" not in resilience:
        return data
    legacy = resilience["backend"]
    data = dict(data)
    data["broker"] = {
        **broker,
        "resilience": {k: v for k, v in resilience.items() if k != "backend"},
    }
    current = data.get("state")
    # A programmatic caller may pass model instances rather than mappings.
    if isinstance(legacy, BaseModel):
        legacy = legacy.model_dump(exclude_unset=True)
    if isinstance(current, BaseModel):
        current = current.model_dump(exclude_unset=True)
    if not isinstance(legacy, dict):
        raise ValueError(
            f"{_DEPRECATED_STATE_PATH} must be a mapping; move it to the top-level state section"
        )
    if not (current is None or isinstance(current, dict)):
        raise ValueError("state must be a mapping")
    current = current or {}
    if current:
        _reject_conflicting_state_sections(current, legacy)
    _warn_deprecated_state_section_once(both=bool(current))
    data["state"] = _deep_merge(legacy, current)
    return data


def _reject_conflicting_state_sections(current: dict[str, Any], legacy: dict[str, Any]) -> None:
    """Raise when a key set under both ``state`` and the old section disagrees.

    Both merge orders are validated: they produce the same backend config only
    when every key set in both agrees. When exactly one order validates, a key
    set in both carries a value that is invalid on one side, which is a
    disagreement too: letting ``state`` win would silently drop it. When
    neither validates, the config is invalid whichever side wins, and the main
    validation reports it under ``state``.
    """
    merged_new_wins = _deep_merge(legacy, current)
    merged_old_wins = _deep_merge(current, legacy)
    new_wins = _validated_state_section(merged_new_wins)
    old_wins = _validated_state_section(merged_old_wins)
    if new_wins is None and old_wins is None:
        return
    if new_wins is not None and old_wins is not None:
        if new_wins == old_wins:
            return
        differing = sorted(
            name
            for name in StateBackendConfig.model_fields
            if getattr(new_wins, name) != getattr(old_wins, name)
        )
    else:
        differing = sorted(
            name
            for name in current.keys() & legacy.keys()
            if merged_new_wins.get(name) != merged_old_wins.get(name)
        )
    raise ValueError(
        f"state and the deprecated {_DEPRECATED_STATE_PATH} both set "
        f"{', '.join(differing)} to different values (check the config file and the "
        "JENTIC__STATE__* / JENTIC__BROKER__RESILIENCE__BACKEND__* env vars); remove "
        f"{_DEPRECATED_STATE_PATH} and keep the value under state"
    )


def _validated_state_section(data: dict[str, Any]) -> StateBackendConfig | None:
    """``data`` as a :class:`StateBackendConfig`, or None when it does not validate."""
    try:
        return StateBackendConfig.model_validate(data)
    except ValidationError:
        return None


def _warn_deprecated_state_section_once(*, both: bool) -> None:
    """One deprecation WARNING per process (config is validated more than once)."""
    if _deprecated_state_section_warned.is_set():
        return
    _deprecated_state_section_warned.set()
    _logger.warning(
        "config_deprecated_setting",
        setting=_DEPRECATED_STATE_PATH,
        replacement="state",
        merged_with_state=both,
        detail=(
            "the shared-state backend moved to the top-level state section; the old "
            "section is still read into it for one release"
        ),
        actionable_step=(
            f"Move {_DEPRECATED_STATE_PATH}.* to state.* in the config file, and rename "
            "JENTIC__BROKER__RESILIENCE__BACKEND__* env vars to JENTIC__STATE__*."
        ),
    )
