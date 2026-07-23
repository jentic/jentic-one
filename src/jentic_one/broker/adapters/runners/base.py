"""The ``UpstreamRunner`` seam (RN-0) and runner request/result value objects.

The execution path is *runner-shaped from day one* (plan.md RN-0): the base
transport is an ``UpstreamRunner``, and every later resilience capability
(idempotency / retry / circuit / deadline) lands as a **composable decorator**
around it rather than an inline branch in the request handler. This is the
extension point the whole roadmap plugs into.

This module is pure (no transport import) so services can depend on the
``UpstreamRunner`` protocol without pulling in ``httpx``; the concrete
``HttpRunner`` lives in ``adapters/runners/http.py``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jentic_one.shared.broker.execution import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.shared.broker.protocols import RunnerCapabilities, Verb
from jentic_one.shared.models.credentials import CredentialType

__all__ = [
    "HTTP_RUNNER_CAPABILITIES",
    "CapabilityAwareRunner",
    "RunnerRequest",
    "RunnerResult",
    "StreamingResult",
    "StreamingUpstreamRunner",
    "UpstreamRunner",
    "capabilities_of",
]


# The capability profile of the default HTTP runner. HTTP is the broker's core
# transport and supports the whole always-on + capability-gated envelope: every
# wire credential type, async durability, idempotency replay, and automatic
# retries. ``one_shot_only`` is False (it's a request/response transport, not a
# fire-and-forget publish) and ``max_payload_bytes`` is 0 (unbounded here — the
# response-size cap is enforced in ``HttpRunner`` itself, not via capabilities).
HTTP_RUNNER_CAPABILITIES = RunnerCapabilities(
    verbs=frozenset({Verb.GET, Verb.PUT, Verb.POST, Verb.DELETE}),
    credential_types=frozenset(CredentialType),
    one_shot_only=False,
    max_payload_bytes=0,
    supports_async=True,
    supports_idempotency=True,
    supports_retries=True,
)


@runtime_checkable
class CapabilityAwareRunner(UpstreamRunner, Protocol):
    """An ``UpstreamRunner`` that declares its :class:`RunnerCapabilities`.

    The composition root (``build_runner``) consults this to gate the
    capability-dependent envelope layers (retry, idempotency) so a runner that
    can't safely support a layer is never wrapped in it (§11 RN-0.3 "envelope
    split by capability"). A runner that does **not** implement this is treated
    by :func:`capabilities_of` as the conservative default (no gated layers).
    """

    def capabilities(self) -> RunnerCapabilities: ...


def capabilities_of(runner: UpstreamRunner) -> RunnerCapabilities:
    """Return ``runner``'s declared capabilities, or a conservative default.

    A runner that implements :class:`CapabilityAwareRunner` reports its own
    profile; one that does not is assumed to support **no** capability-gated
    layer (retries/idempotency/async off), so an undeclared runner is never
    wrapped in a layer it may not handle safely.
    """
    if isinstance(runner, CapabilityAwareRunner):
        return runner.capabilities()
    return RunnerCapabilities(
        verbs=frozenset(),
        credential_types=frozenset(),
        one_shot_only=True,
        max_payload_bytes=0,
        supports_async=False,
        supports_idempotency=False,
        supports_retries=False,
    )
