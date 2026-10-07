"""Resilience envelope: per-caller rate limiting + per-upstream circuit.

These are thin **policy** layers over the shared-state backend: the rate
limiter wraps a ``RateLimitStore`` token bucket and the circuit breaker wraps an
``AtomicStore`` counter + latch. Selecting ``memory`` vs ``redis`` at the backend
(``shared/state``) is what makes the same policy per-instance or cluster-wide —
no call-site change. Consumers depend on these small classes, never on a
concrete backend.
"""

from __future__ import annotations

from jentic_one.shared.resilience.circuit import (
    CircuitBreaker,
    CircuitDecision,
    CircuitState,
)
from jentic_one.shared.resilience.rate_limit import RateLimiter, RateLimitOutcome

__all__ = [
    "CircuitBreaker",
    "CircuitDecision",
    "CircuitState",
    "RateLimitOutcome",
    "RateLimiter",
]
