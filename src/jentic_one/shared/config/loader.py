"""Config loading: extension registry, environment overrides, and ``load_config``."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Any, cast

import structlog
import yaml
from pydantic import BaseModel, ValidationError

from jentic_one.shared.config._common import ConfigError, _deep_merge
from jentic_one.shared.config._secrets import _ENABLED_APPS
from jentic_one.shared.config.app import AppConfig

_logger = structlog.get_logger(__name__)


# --- Extension config registry -----------------------------------------------
# A downstream package registers extra sub-config models at import time; by
# default the registry is empty. Keyed by the top-level YAML/env section name.
# load_config() pulls any matching top-level key out of the merged config and
# validates it with the registered model, storing the result in
# AppConfig.extensions[name].
_CONFIG_EXTENSIONS: dict[str, type[BaseModel]] = {}


def register_config(name: str, model: type[BaseModel]) -> None:
    """Register an extension sub-config model under a top-level config key.

    Idempotent for the same (name, model); raises on a conflicting re-register so
    two extensions can't fight over one key. Call at import time (e.g. in a
    registering package's __init__) before load_config() runs.
    """
    # Collision guard: an extension key must not shadow a core AppConfig field
    # (e.g. "broker", "search") nor the reserved "extensions" container itself —
    # either would break the parser or silently override core config.
    if name in AppConfig.model_fields or name == "extensions":
        raise ConfigError(
            f"Config extension name {name!r} collides with a core AppConfig field "
            "or the reserved 'extensions' key"
        )
    existing = _CONFIG_EXTENSIONS.get(name)
    if existing is not None and existing is not model:
        raise ConfigError(f"Config extension {name!r} already registered to {existing!r}")
    _CONFIG_EXTENSIONS[name] = model


def registered_config_models() -> dict[str, type[BaseModel]]:
    """Snapshot of the extension registry (for tests/introspection)."""
    return dict(_CONFIG_EXTENSIONS)


def _coerce_indexed_dicts_to_lists(value: Any) -> Any:
    """Recursively turn digit-keyed dicts into lists.

    The env convention (``JENTIC__SECTION__KEY``) can only ever build nested
    dicts, so a *list*-valued field addressed by index —
    ``JENTIC__AUTH__ID_SIGNING__0__KID`` — arrives as ``{"0": {"kid": ...}}``
    rather than ``[{"kid": ...}]`` and fails validation with ``list_type``.

    A dict is treated as a list when its keys are exactly the contiguous integer
    sequence ``0..n-1`` (as strings); it's then rebuilt in index order. Any other
    dict (real string keys, or a sparse/1-based set) is left untouched and
    recursed into, so ordinary config is unaffected.
    """
    if isinstance(value, dict):
        coerced = {k: _coerce_indexed_dicts_to_lists(v) for k, v in value.items()}
        keys = list(coerced.keys())
        if keys and all(k.isdigit() for k in keys):
            ordered = sorted(keys, key=int)
            if [int(k) for k in ordered] == list(range(len(ordered))):
                return [coerced[k] for k in ordered]
        return coerced
    if isinstance(value, list):
        return [_coerce_indexed_dicts_to_lists(item) for item in value]
    return value


def _env_overrides() -> dict[str, Any]:
    """Build a nested dict from JENTIC__* environment variables.

    Convention: JENTIC__SECTION__KEY=value → {"section": {"key": "value"}}

    A numeric path segment addresses a list index, so
    ``JENTIC__AUTH__ID_SIGNING__0__KID`` builds ``{"0": {...}}`` here and is
    coerced to a one-element list by :func:`_coerce_indexed_dicts_to_lists`.
    """
    prefix = "JENTIC__"
    result: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = [p.lower() for p in key[len(prefix) :].split("__")]
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
    # Top-level keys are section names (never all-digit), so the result stays a
    # dict; the coercion only reshapes nested indexed segments.
    return cast("dict[str, Any]", _coerce_indexed_dicts_to_lists(result))


# One-shot config sources (pipes / inherited fds) cached per process. A config
# handed on a pipe — e.g. ``JENTIC_CONFIG_FILE=/dev/fd/3`` from a supervisor
# that keeps secrets out of the filesystem, argv, and env — can only be read
# once, but several consumers load config more than once per process (the
# Alembic env loads it per database). The first read is cached so later loads
# see the same document. Regular files keep re-reading from disk.
#
# The cache key is the source's stat identity (device, inode, ctime) plus the
# path string, never the path alone: fd numbers are recycled by the kernel, so
# two different files can appear as the same ``/dev/fd/N`` within one process,
# and a path-string key would silently serve the first file's contents for the
# second. Keying on identity also keeps ``/dev/fd/N`` of a *regular* file
# re-readable (rotation-safe) and covers ``/proc/self/fd/N`` spellings.
#
# (device, inode) alone is not a stable identity: macOS reports pipes with
# st_dev 0 and recycles a freed pipe's st_ino for the next pipe, and a deleted
# and recreated named FIFO can land on a reused inode. The ctime (set at
# creation, bumped by every write, frozen once the writer closes) separates
# those. Linux pipe ctimes are tick-granular and can coincide across pipes, but
# Linux pipe inodes are not recycled, so the tuple stays distinct on both.
# (#1499)
_ONESHOT_CONFIG_CACHE: dict[tuple[int, int, int, str], str] = {}
_ONESHOT_CACHE_LOCK = threading.Lock()

# A forked child never received the one-shot document itself; drop the parent's
# copy so it cannot linger in (or leak from) a process it was never handed to.
os.register_at_fork(after_in_child=_ONESHOT_CONFIG_CACHE.clear)


def oneshot_config_source_active() -> bool:
    """True when this process's config came from a one-shot source (pipe, /dev/fd).

    A one-shot source serves exactly one process: a child process (e.g. a
    reload worker) cannot re-read it. Entry points that spawn config-loading
    children consult this to fail or degrade loudly instead of hanging.
    """
    return bool(_ONESHOT_CONFIG_CACHE)


def _oneshot_cache_key(st: os.stat_result, path: Path) -> tuple[int, int, int, str]:
    return (st.st_dev, st.st_ino, st.st_ctime_ns, str(path))


def _read_config_text(path: Path) -> str:
    """Read the config document, caching one-shot sources (pipes, /dev/fd)."""
    # Cache lookup by stat identity happens before open(): opening a named FIFO
    # whose writer has gone blocks forever, and a cache hit must not reopen it.
    st = os.stat(path)
    if stat.S_ISFIFO(st.st_mode) or stat.S_ISSOCK(st.st_mode):
        key = _oneshot_cache_key(st, path)
        with _ONESHOT_CACHE_LOCK:
            if key in _ONESHOT_CONFIG_CACHE:
                # "Why didn't my config change take effect" needs a trail: a
                # one-shot source is served from the process cache, not re-read.
                _logger.info("oneshot_config_cache_reused", source=str(path))
                return _ONESHOT_CONFIG_CACHE[key]
    with path.open() as f:
        fst = os.fstat(f.fileno())
        # Decide "one-shot" from the opened fd itself: FIFOs, sockets, and any
        # non-seekable stream can be read exactly once. Seekable sources are
        # regular files (wherever the path points) and are re-read every load.
        if f.seekable() and not (stat.S_ISFIFO(fst.st_mode) or stat.S_ISSOCK(fst.st_mode)):
            # /dev/fd/N re-opens share the underlying file offset on macOS (dup
            # semantics), so rewind before reading.
            f.seek(0)
            return f.read()
        # The drain-and-cache is a single critical section: a concurrent loader
        # must wait and take the cached document, never race the pipe read.
        with _ONESHOT_CACHE_LOCK:
            # Stat inside the lock: a loader that waited here while another
            # drained a still-streaming source must see the post-drain ctime
            # the drainer stored, not the one from before the lock.
            key = _oneshot_cache_key(os.fstat(f.fileno()), path)
            if key in _ONESHOT_CONFIG_CACHE:
                _logger.info("oneshot_config_cache_reused", source=str(path))
                return _ONESHOT_CONFIG_CACHE[key]
            text = f.read()
            if not text.strip():
                # Caching an empty read would pin the process to a broken
                # config with no recovery; failing here names the real cause
                # instead of a downstream "Field required" error.
                raise ConfigError(
                    f"one-shot config source {str(path)!r} yielded no content — it was "
                    "already drained by another reader, or the supervisor closed it "
                    "without writing"
                )
            # Every write bumps the ctime, so the stored key is taken after the
            # drain, once the writer has closed: later loads see the same
            # frozen identity.
            key = _oneshot_cache_key(os.fstat(f.fileno()), path)
            return _ONESHOT_CONFIG_CACHE.setdefault(key, text)


def load_config(path: Path | None = None) -> AppConfig:
    """Load and validate application configuration.

    Resolution order:
    1. YAML file (explicit path > JENTIC_CONFIG_FILE env var > ./jentic-one.yaml)
    2. Environment variable overrides (JENTIC__SECTION__KEY)

    Raises ConfigError on validation failure.
    """
    file_data: dict[str, Any] = {}

    config_path = path
    if config_path is None:
        env_path = os.environ.get("JENTIC_CONFIG_FILE")
        if env_path:
            config_path = Path(env_path)
        else:
            default_path = Path("jentic-one.yaml")
            if default_path.exists():
                config_path = default_path

    if config_path is not None:
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
        loaded = yaml.safe_load(_read_config_text(config_path))
        if isinstance(loaded, dict):
            file_data = loaded

    env_data = _env_overrides()
    merged = _deep_merge(file_data, env_data)

    if "apps" in merged and isinstance(merged["apps"], str):
        merged["apps"] = [item.strip() for item in merged["apps"].split(",") if item.strip()]

    # Extract registered extension sections before validating the core model, so
    # extra="forbid" accepts them and each is validated by its own model.
    extensions: dict[str, BaseModel] = {}
    for name, model in _CONFIG_EXTENSIONS.items():
        if name in merged:
            raw = merged.pop(name)
            try:
                extensions[name] = model.model_validate(raw)
            except ValidationError as e:
                raise ConfigError(f"Invalid config for extension {name!r}: {e}") from e
    if extensions:
        merged["extensions"] = extensions

    # Let the production secret guard see which surfaces this config runs, so a
    # standalone surface is only required to carry the secrets it reads. Nested
    # sections built by default_factory validate inside this call too, so a
    # contextvar (not pydantic's validation context) reaches all of them.
    token = _ENABLED_APPS.set(_apps_for_secret_guard(merged.get("apps")))
    try:
        return AppConfig.model_validate(merged)
    except Exception as e:
        raise ConfigError(f"Configuration validation failed: {e}") from e
    finally:
        _ENABLED_APPS.reset(token)


def _apps_for_secret_guard(raw: Any) -> frozenset[str] | None:
    """The surface set the secret guard should assume for a raw ``apps`` value.

    Absent means the ``AppConfig.apps`` default; an empty list or anything that
    is not a list of strings returns None (strict guard).
    """
    if raw is None:
        raw = AppConfig.model_fields["apps"].get_default(call_default_factory=True)
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        return None
    return frozenset(raw)
