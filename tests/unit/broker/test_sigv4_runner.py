"""Unit tests for the ``SigV4SigningRunner`` decorator (#776).

SigV4 signing is a transport-edge decorator: it signs the final (method, URL,
body) immediately before dispatch and is a transparent passthrough when the
request carries no signing material. It must also strip the material after
signing so it cannot travel further or land in a repr.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from jentic_one.broker.adapters.runners.base import (
    HTTP_RUNNER_CAPABILITIES,
    RunnerRequest,
    RunnerResult,
    StreamingResult,
)
from jentic_one.broker.adapters.runners.sigv4 import SigV4SigningRunner
from jentic_one.shared.aws.sigv4 import SigV4Material


class _CapturingRunner:
    """Records the request it was handed so we can assert the signed output."""

    def __init__(self) -> None:
        self.seen: RunnerRequest | None = None

    def capabilities(self) -> object:
        return HTTP_RUNNER_CAPABILITIES

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.seen = request
        return RunnerResult(
            status_code=200, body=b"ok", headers={}, content_type=None, duration_ms=1
        )

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        self.seen = request
        yield StreamingResult(status_code=200, headers={}, content_type=None, aiter=_empty_aiter())


async def _empty_aiter() -> AsyncIterator[bytes]:
    return
    yield  # pragma: no cover - makes this an async generator


_MATERIAL = SigV4Material(
    access_key_id="AKIDEXAMPLE",
    secret_access_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
    region="us-east-1",
    service="aoss",
)


@pytest.mark.asyncio()
async def test_passthrough_when_no_signing_material() -> None:
    inner = _CapturingRunner()
    runner = SigV4SigningRunner(inner)
    req = RunnerRequest(method="GET", url="https://x.example/", headers={"a": "b"})

    await runner.run(req)

    assert inner.seen is req  # untouched — identical object forwarded
    assert "authorization" not in inner.seen.headers


@pytest.mark.asyncio()
async def test_signs_and_merges_headers_when_material_present() -> None:
    inner = _CapturingRunner()
    runner = SigV4SigningRunner(inner)
    req = RunnerRequest(
        method="GET",
        url="https://collection.us-east-1.aoss.amazonaws.com/_search",
        headers={"accept": "application/json"},
        signing=_MATERIAL,
    )

    await runner.run(req)

    assert inner.seen is not None
    headers = inner.seen.headers
    assert headers["accept"] == "application/json"  # existing header preserved
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "x-amz-date" in headers
    assert "x-amz-content-sha256" in headers


@pytest.mark.asyncio()
async def test_signing_material_stripped_after_signing() -> None:
    inner = _CapturingRunner()
    runner = SigV4SigningRunner(inner)
    req = RunnerRequest(
        method="POST",
        url="https://x.aoss.amazonaws.com/",
        body=b'{"q":1}',
        signing=_MATERIAL,
    )

    await runner.run(req)

    # The consumed material must not travel further (defence-in-depth: it also
    # keeps the secret out of any downstream repr).
    assert inner.seen is not None
    assert inner.seen.signing is None


@pytest.mark.asyncio()
async def test_original_request_not_mutated() -> None:
    inner = _CapturingRunner()
    runner = SigV4SigningRunner(inner)
    req = RunnerRequest(method="GET", url="https://x.aoss.amazonaws.com/", signing=_MATERIAL)

    await runner.run(req)

    # The caller's request is unchanged; signing produced a new frozen instance.
    assert req.signing is _MATERIAL
    assert "authorization" not in req.headers


@pytest.mark.asyncio()
async def test_capabilities_forwarded_from_inner() -> None:
    inner = _CapturingRunner()
    runner = SigV4SigningRunner(inner)
    assert runner.capabilities() == HTTP_RUNNER_CAPABILITIES


@pytest.mark.asyncio()
async def test_stream_signs_request() -> None:
    inner = _CapturingRunner()
    runner = SigV4SigningRunner(inner)
    req = RunnerRequest(method="GET", url="https://x.aoss.amazonaws.com/", signing=_MATERIAL)

    async with runner.stream(req) as result:
        assert result.status_code == 200

    assert inner.seen is not None
    assert "authorization" in inner.seen.headers
    assert inner.seen.signing is None
