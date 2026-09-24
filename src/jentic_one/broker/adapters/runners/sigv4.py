"""``SigV4SigningRunner`` — signs SigV4-credentialed requests at the transport edge.

AWS SigV4 signs the *final* request (method, canonical URL + query, a fixed set
of headers, and the payload hash), which are only settled once server-variable
substitution and query merging have run. So signing cannot be a static header
produced during credential injection — it is a decorator wrapped around the
transport runner, applied to the ``RunnerRequest`` immediately before dispatch.

Placement matters: this runner sits **inside** the retry loop (it wraps the base
transport runner, and ``build_runner`` wraps *this* in retry/deadline). Each
retry attempt therefore re-signs with a fresh ``x-amz-date`` — a backoff sleep
must not push a reused signature outside AWS's clock-skew window. Requests
without ``signing`` material pass straight through untouched.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace

from jentic_one.broker.adapters.runners.base import capabilities_of
from jentic_one.shared.aws.sigv4 import sign_request
from jentic_one.shared.broker.execution import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.shared.broker.protocols import RunnerCapabilities


class SigV4SigningRunner(UpstreamRunner):
    """Wraps a runner, signing requests that carry SigV4 material."""

    def __init__(self, inner: UpstreamRunner) -> None:
        self._inner = inner

    def capabilities(self) -> RunnerCapabilities:
        """Forward the inner runner's capabilities so envelope gating is preserved.

        Without this, ``capabilities_of`` would treat the wrapped runner as an
        undeclared runner and strip retry/idempotency/async support.
        """
        return capabilities_of(self._inner)

    @staticmethod
    def _signed(request: RunnerRequest) -> RunnerRequest:
        if request.signing is None:
            return request
        sig_headers = sign_request(
            method=request.method,
            url=request.url,
            body=request.body,
            material=request.signing,
        )
        # Merge signed headers over the outbound set, matching header names
        # case-insensitively: a forwarded ``Authorization`` (capital A) must be
        # *replaced* by our ``authorization``, not duplicated into two headers
        # that AWS would reject. Then drop the now-consumed signing material so it
        # never travels further or lands in a repr.
        signed_lower = {name.lower() for name in sig_headers}
        merged = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in signed_lower
        }
        merged.update(sig_headers)
        return replace(request, headers=merged, signing=None)

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return await self._inner.run(self._signed(request))

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        inner = self._inner
        if not isinstance(inner, StreamingUpstreamRunner):
            # Control-flow guard, not a debug check: an explicit raise survives
            # ``python -O`` (which strips ``assert``). Reaching here means the
            # runner chain was composed with a non-streaming inner runner.
            raise TypeError(
                f"{type(inner).__name__} does not support streaming; "
                "SigV4SigningRunner.stream requires a StreamingUpstreamRunner"
            )
        async with inner.stream(self._signed(request)) as result:
            yield result
