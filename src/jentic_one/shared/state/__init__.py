"""Shared-state backend package: role-segregated stores and the backend registry."""

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
    key_namespace,
)
from jentic_one.shared.state.factory import (
    MEMORY_BACKEND,
    REDIS_BACKEND,
    MemoryBackendConfig,
    MemoryNamespaceBudgetConfig,
    StateBackendBuildContext,
    StateBackendBuilder,
    StateBackendConfig,
    build_state_backend,
    register_state_backend,
    registered_state_backends,
)

__all__ = [
    "MEMORY_BACKEND",
    "REDIS_BACKEND",
    "AtomicStore",
    "KeyValueStore",
    "MemoryBackendConfig",
    "MemoryNamespaceBudgetConfig",
    "MemoryStateBackend",
    "NamespaceBudget",
    "RateLimitDecision",
    "RateLimitStore",
    "SharedStateBackend",
    "StateBackendBuildContext",
    "StateBackendBuilder",
    "StateBackendCapacityError",
    "StateBackendConfig",
    "build_state_backend",
    "key_namespace",
    "register_state_backend",
    "registered_state_backends",
]
