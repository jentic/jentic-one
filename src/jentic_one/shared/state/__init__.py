"""Shared-state backend package: role-segregated stores (memory | redis)."""

from __future__ import annotations

from jentic_one.shared.state.backend import (
    AtomicStore,
    KeyValueStore,
    MemoryStateBackend,
    NamespaceBudget,
    RateLimitDecision,
    RateLimitStore,
    SharedStateBackend,
    StateBackendCapacityError,
    WindowCounterStore,
    key_namespace,
)
from jentic_one.shared.state.factory import (
    BackendKind,
    MemoryBackendConfig,
    MemoryNamespaceBudgetConfig,
    StateBackendConfig,
    build_state_backend,
)

__all__ = [
    "AtomicStore",
    "BackendKind",
    "KeyValueStore",
    "MemoryBackendConfig",
    "MemoryNamespaceBudgetConfig",
    "MemoryStateBackend",
    "NamespaceBudget",
    "RateLimitDecision",
    "RateLimitStore",
    "SharedStateBackend",
    "StateBackendCapacityError",
    "StateBackendConfig",
    "WindowCounterStore",
    "build_state_backend",
    "key_namespace",
]
