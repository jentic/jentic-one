"""Shared-state backend package: role-segregated stores (memory | redis)."""

from __future__ import annotations

from jentic_one.shared.state.backend import (
    AtomicStore,
    KeyValueStore,
    MemoryStateBackend,
    RateLimitDecision,
    RateLimitStore,
    SharedStateBackend,
    StateCapacityError,
    WindowCounterStore,
)
from jentic_one.shared.state.factory import (
    BackendKind,
    StateBackendConfig,
    build_state_backend,
)

__all__ = [
    "AtomicStore",
    "BackendKind",
    "KeyValueStore",
    "MemoryStateBackend",
    "RateLimitDecision",
    "RateLimitStore",
    "SharedStateBackend",
    "StateBackendConfig",
    "StateCapacityError",
    "WindowCounterStore",
    "build_state_backend",
]
