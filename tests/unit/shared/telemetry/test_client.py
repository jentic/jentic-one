"""Unit tests for the telemetry HTTP client — wire shape + failure swallowing."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.events import ErrorSource
from jentic_one.shared.telemetry.client import TelemetryClient
from jentic_one.shared.telemetry.events import TelemetryEvent, TelemetryEventName


def _client(handler: httpx.MockTransport) -> TelemetryClient:
    client = TelemetryClient(
        endpoint="https://api.jentic.com",
        instance_id="inst-123",
        version="1.4.2",
        request_timeout_s=5.0,
    )
    client._client = httpx.AsyncClient(transport=handler)
    return client


@pytest.mark.asyncio
async def test_request_shape_is_exactly_id_version_event_ts() -> None:
    """An untagged event with no actor_type POSTs exactly {id, version, event, ts}."""
    captured: list[dict[str, object]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        captured.append(json.loads(req.content))
        return httpx.Response(201)

    client = _client(httpx.MockTransport(handle))
    event = TelemetryEvent(
        name=TelemetryEventName.CREDENTIAL_STORED,
        tags=(),
        ts=datetime(2026, 6, 29, 13, 0, 0, tzinfo=UTC),
    )

    await client.send([event])
    await client.aclose()

    assert len(captured) == 1
    assert set(captured[0]) == {"id", "version", "event", "ts"}
    assert captured[0]["id"] == "inst-123"
    assert captured[0]["version"] == "1.4.2"
    assert captured[0]["event"] == "credential_stored"


@pytest.mark.asyncio
async def test_actor_type_included_and_normalised() -> None:
    """actor_type is included when set, serialised as the enum value."""
    captured: list[dict[str, object]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        captured.append(json.loads(req.content))
        return httpx.Response(201)

    client = _client(httpx.MockTransport(handle))
    for actor_type, _expected in [
        (ActorType.AGENT, "agent"),
        (ActorType.USER, "user"),
    ]:
        event = TelemetryEvent(
            name=TelemetryEventName.BROKER_EXECUTION,
            tags=(),
            ts=datetime(2026, 6, 29, 13, 0, 0, tzinfo=UTC),
            actor_type=actor_type,
        )
        await client.send([event])

    await client.aclose()

    assert captured[0]["actor_type"] == "agent"
    assert captured[1]["actor_type"] == "user"


@pytest.mark.asyncio
async def test_tags_included_only_when_present() -> None:
    """A tagged event carries every closed-enum tag value as a list of strings."""
    captured: list[dict[str, object]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        captured.append(json.loads(req.content))
        return httpx.Response(201)

    client = _client(httpx.MockTransport(handle))
    event = TelemetryEvent(
        name=TelemetryEventName.CREDENTIAL_REFRESH_FAILED,
        tags=(ErrorSource.AUTH_JENTIC, ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED),
        ts=datetime(2026, 6, 29, 13, 0, 0, tzinfo=UTC),
    )

    await client.send([event])
    await client.aclose()

    assert set(captured[0]) == {"id", "version", "event", "tags", "ts"}
    assert captured[0]["tags"] == ["auth_jentic", "auth_thirdparty_unauthorized"]


@pytest.mark.asyncio
async def test_send_swallows_transport_failure() -> None:
    """A transport error must never propagate out of send."""

    def handle(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(httpx.MockTransport(handle))
    event = TelemetryEvent(
        name=TelemetryEventName.INSTANCE_BOOTED,
        tags=(),
        ts=datetime.now(UTC),
    )

    # Must not raise.
    await client.send([event])
    await client.aclose()
