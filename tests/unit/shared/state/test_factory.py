"""Unit tests for the shared-state backend registry and factory."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog.testing
from pydantic import SecretStr, ValidationError

from jentic_one.shared.state import (
    MEMORY_BACKEND,
    REDIS_BACKEND,
    MemoryStateBackend,
    SharedStateBackend,
    StateBackendBuildContext,
    StateBackendConfig,
    build_state_backend,
    register_state_backend,
    registered_state_backends,
)
from jentic_one.shared.state.factory import unregister_state_backend


@pytest.fixture()
def custom_backend() -> Iterator[list[StateBackendBuildContext]]:
    """Register ``custom`` for one test; yields the contexts its builder saw."""
    seen: list[StateBackendBuildContext] = []

    def _build(ctx: StateBackendBuildContext) -> SharedStateBackend:
        seen.append(ctx)
        return MemoryStateBackend()

    register_state_backend("custom", _build)
    try:
        yield seen
    finally:
        unregister_state_backend("custom")


def test_memory_is_default() -> None:
    assert StateBackendConfig().backend == MEMORY_BACKEND
    assert isinstance(build_state_backend(StateBackendConfig()), MemoryStateBackend)


def test_memory_backend_uses_the_memory_budgets() -> None:
    cfg = StateBackendConfig.model_validate({"memory": {"max_total_bytes": 4096}})
    backend = build_state_backend(cfg)
    assert isinstance(backend, MemoryStateBackend)
    assert backend._max_total_bytes == 4096


def test_built_in_backends_are_registered() -> None:
    assert {MEMORY_BACKEND, REDIS_BACKEND} <= set(registered_state_backends())


def test_a_registered_backend_is_selectable_and_gets_its_context(
    custom_backend: list[StateBackendBuildContext],
) -> None:
    cfg = StateBackendConfig.model_validate({"backend": "custom", "options": {"url": "x://y"}})
    backend = build_state_backend(cfg, apps=frozenset({"broker"}))
    assert isinstance(backend, MemoryStateBackend)
    (ctx,) = custom_backend
    assert ctx.config is cfg
    assert ctx.config.options == {"url": "x://y"}
    assert ctx.apps == frozenset({"broker"})
    assert "custom" in registered_state_backends()


def test_an_unknown_backend_fails_naming_the_registered_ones() -> None:
    cfg = StateBackendConfig(backend="nope")
    with pytest.raises(RuntimeError, match="unknown shared-state backend 'nope'") as exc:
        build_state_backend(cfg)
    assert "memory, redis" in str(exc.value)
    assert "register_state_backend" in str(exc.value)


def test_registration_is_idempotent_for_the_same_builder(
    custom_backend: list[StateBackendBuildContext],
) -> None:
    def _other(ctx: StateBackendBuildContext) -> SharedStateBackend:
        return MemoryStateBackend()

    register_state_backend("other", _other)
    try:
        register_state_backend("other", _other)
        with pytest.raises(ValueError, match="already registered"):
            register_state_backend("other", lambda ctx: MemoryStateBackend())
    finally:
        unregister_state_backend("other")


@pytest.mark.parametrize("name", [MEMORY_BACKEND, REDIS_BACKEND])
def test_built_in_backends_cannot_be_replaced(name: str) -> None:
    with pytest.raises(ValueError, match="built in"):
        register_state_backend(name, lambda ctx: MemoryStateBackend())


@pytest.mark.parametrize("name", ["", "Redis", "-x", "a b", "a:b"])
def test_backend_names_are_validated(name: str) -> None:
    with pytest.raises(ValueError, match="invalid state backend name"):
        register_state_backend(name, lambda ctx: MemoryStateBackend())
    with pytest.raises(ValidationError):
        StateBackendConfig(backend=name)


def test_redis_without_url_fails_fast() -> None:
    cfg = StateBackendConfig(backend=REDIS_BACKEND, redis_url=None)
    with pytest.raises(RuntimeError, match="redis_url"):
        build_state_backend(cfg)


def test_redis_logs_a_deprecation_warning() -> None:
    cfg = StateBackendConfig(
        backend=REDIS_BACKEND,
        redis_url=SecretStr("redis://localhost:6379/0"),
        redis_key_prefix="jentic:broker:prod:",
    )
    with structlog.testing.capture_logs() as logs:
        build_state_backend(cfg)
    deprecations = [e for e in logs if e["event"] == "state_backend_deprecated"]
    assert len(deprecations) == 1
    assert deprecations[0]["log_level"] == "warning"
    assert deprecations[0]["backend"] == REDIS_BACKEND


def test_memory_logs_no_deprecation_warning() -> None:
    with structlog.testing.capture_logs() as logs:
        build_state_backend(StateBackendConfig())
    assert not [e for e in logs if e["event"] == "state_backend_deprecated"]


def test_redis_default_prefix_warns() -> None:
    """Selecting redis with the unchanged default prefix emits a startup warning."""
    cfg = StateBackendConfig(backend=REDIS_BACKEND, redis_url=SecretStr("redis://localhost:6379/0"))
    with structlog.testing.capture_logs() as logs:
        backend = build_state_backend(cfg)
    assert backend is not None
    warnings = [e for e in logs if e["event"] == "state_backend_default_redis_key_prefix"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"


def test_redis_custom_prefix_does_not_warn() -> None:
    cfg = StateBackendConfig(
        backend=REDIS_BACKEND,
        redis_url=SecretStr("redis://localhost:6379/0"),
        redis_key_prefix="jentic:broker:prod:",
    )
    with structlog.testing.capture_logs() as logs:
        build_state_backend(cfg)
    assert not [e for e in logs if e["event"] == "state_backend_default_redis_key_prefix"]
