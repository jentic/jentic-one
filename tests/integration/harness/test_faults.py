"""Self-tests for the X-Mock-* fault-injection middleware."""

from __future__ import annotations

import time

import httpx
import pytest
from httpx import AsyncClient

from tests.harness.smoke_upstream import mock_control
from tests.harness.smoke_upstream.mock_control import (
    HEADER_DELAY,
    HEADER_DISCONNECT,
    HEADER_STATUS,
    HEADER_STATUS_SEQUENCE,
    HEADER_TEST_ID,
)


async def test_mock_status_returns_requested_code(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post("/behavior/echo", headers={HEADER_STATUS: "503"})
    assert response.status_code == 503


async def test_mock_delay_measurably_delays(smoke_client: AsyncClient) -> None:
    delay = 0.05
    start = time.perf_counter()
    response = await smoke_client.post("/behavior/echo", headers={HEADER_DELAY: str(delay)})
    elapsed = time.perf_counter() - start

    assert response.status_code == 200
    assert elapsed >= delay


async def test_mock_status_sequence_advances_per_test_id(smoke_client: AsyncClient) -> None:
    headers = {HEADER_STATUS_SEQUENCE: "503,502,200", HEADER_TEST_ID: "seq-a"}
    codes = [
        (await smoke_client.post("/behavior/echo", headers=headers)).status_code for _ in range(3)
    ]
    assert codes == [503, 502, 200]


async def test_mock_status_sequence_repeats_last_when_exhausted(smoke_client: AsyncClient) -> None:
    headers = {HEADER_STATUS_SEQUENCE: "500,200", HEADER_TEST_ID: "seq-exhaust"}
    codes = [
        (await smoke_client.post("/behavior/echo", headers=headers)).status_code for _ in range(4)
    ]
    assert codes == [500, 200, 200, 200]


async def test_mock_status_sequence_independent_across_test_ids(smoke_client: AsyncClient) -> None:
    headers_a = {HEADER_STATUS_SEQUENCE: "503,502,200", HEADER_TEST_ID: "id-1"}
    headers_b = {HEADER_STATUS_SEQUENCE: "401,403", HEADER_TEST_ID: "id-2"}

    first_a = (await smoke_client.post("/behavior/echo", headers=headers_a)).status_code
    first_b = (await smoke_client.post("/behavior/echo", headers=headers_b)).status_code
    second_a = (await smoke_client.post("/behavior/echo", headers=headers_a)).status_code
    second_b = (await smoke_client.post("/behavior/echo", headers=headers_b)).status_code

    assert (first_a, second_a) == (503, 502)
    assert (first_b, second_b) == (401, 403)


async def test_mock_status_sequence_requires_test_id(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post(
        "/behavior/echo", headers={HEADER_STATUS_SEQUENCE: "503,200"}
    )
    assert response.status_code == 400


async def test_mock_status_sequence_cursors_are_bounded(
    smoke_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Load runs mint a fresh test id per logical request; the cursor map is an
    # LRU so it cannot grow without bound. The oldest id is evicted (its cursor
    # restarts) while a recently used one keeps advancing.
    monkeypatch.setattr(mock_control, "_MAX_SEQUENCE_CURSORS", 2)
    seq = {HEADER_STATUS_SEQUENCE: "503,200"}

    async def call(test_id: str) -> int:
        resp = await smoke_client.post("/behavior/echo", headers={**seq, HEADER_TEST_ID: test_id})
        return resp.status_code

    assert await call("lru-a") == 503
    assert await call("lru-b") == 503
    assert await call("lru-c") == 503  # evicts lru-a
    assert len(mock_control._sequence_cursors) == 2
    assert await call("lru-c") == 200  # recent id keeps its cursor
    assert await call("lru-a") == 503  # evicted id restarts its sequence


async def test_mock_disconnect_raises_transport_error(smoke_client: AsyncClient) -> None:
    with pytest.raises(httpx.RemoteProtocolError):
        await smoke_client.post("/behavior/echo", headers={HEADER_DISCONNECT: "true"})


async def test_malformed_mock_status_returns_400(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post("/behavior/echo", headers={HEADER_STATUS: "not-an-int"})
    assert response.status_code == 400
