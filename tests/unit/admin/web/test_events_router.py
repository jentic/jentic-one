"""Unit tests for _event_response, action link resolution, and Last-Event-ID handling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import EventView, Heartbeat
from jentic_one.admin.web.deps import get_event_stream_service
from jentic_one.admin.web.routers.events import (
    _event_response,
    _resolve_action_link,
    router,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.shared.web.errors import make_service_error_handler


def _make_request() -> MagicMock:
    request = MagicMock()
    request.base_url = "http://testserver/"
    return request


def _make_event_view(
    *,
    event_type: str = "import.completed",
    data: dict[str, object] | None = None,
) -> EventView:
    return EventView(
        id="evt_001",
        type=event_type,
        severity=EventSeverity.INFO,
        summary="test event",
        requires_action=event_type == EventType.ACCESS_REQUEST_FILED,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        data=data or {},
    )


async def _fake_identity() -> Identity:
    return Identity(
        sub="usr_test",
        permissions=["org:admin"],
    )


def test_action_link_populated_for_access_request_filed() -> None:
    view = _make_event_view(
        event_type=EventType.ACCESS_REQUEST_FILED,
        data={"request_id": "req-123", "status": "pending"},
    )
    request = _make_request()
    resp = _event_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["_links"]["action"] == "http://testserver/access-requests/req-123:decide"


def test_action_link_none_for_non_actionable_event() -> None:
    view = _make_event_view(event_type="execution.completed")
    request = _make_request()
    resp = _event_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["_links"]["action"] is None


def test_action_link_none_when_request_id_missing() -> None:
    view = _make_event_view(
        event_type=EventType.ACCESS_REQUEST_FILED,
        data={},
    )
    request = _make_request()
    resp = _event_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["_links"]["action"] is None


def test_resolve_action_link_returns_correct_url() -> None:
    view = _make_event_view(
        event_type=EventType.ACCESS_REQUEST_FILED,
        data={"request_id": "abc-def"},
    )
    request = _make_request()
    result = _resolve_action_link(view, request)
    assert result == "http://testserver/access-requests/abc-def:decide"


def test_resolve_action_link_returns_none_for_other_types() -> None:
    view = _make_event_view(event_type=EventType.ACCESS_REQUEST_APPROVED)
    request = _make_request()
    result = _resolve_action_link(view, request)
    assert result is None


def test_actor_fields_present_in_response() -> None:
    view = EventView(
        id="evt_002",
        type="execution.completed",
        severity=EventSeverity.INFO,
        summary="test event",
        requires_action=False,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        data={},
        actor_id="agt_abc",
        actor_type="agent",
    )
    request = _make_request()
    resp = _event_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["actor_id"] == "agt_abc"
    assert data["actor_type"] == "agent"


def test_actor_fields_null_when_absent() -> None:
    view = _make_event_view()
    request = _make_request()
    resp = _event_response(view, request)
    data = resp.model_dump(by_alias=True)
    assert data["actor_id"] is None
    assert data["actor_type"] is None


# --- Last-Event-ID behavioral tests ---


@pytest.fixture()
def mock_stream_service() -> AsyncMock:
    svc = AsyncMock(spec=EventStreamService)

    def _empty_stream(**kwargs: object) -> AsyncIterator[Heartbeat]:
        async def _gen() -> AsyncIterator[Heartbeat]:
            yield Heartbeat(sent_at=datetime.now(UTC))

        return _gen()

    # ``stream`` is a regular (non-async) method returning an async generator,
    # consumed via ``async for`` in the router. Modelling it as ``AsyncMock``
    # would make the call return an un-awaited coroutine (RuntimeWarning), so
    # use a synchronous MagicMock that returns the async iterator directly.
    svc.stream = MagicMock(side_effect=_empty_stream)
    return svc


@pytest.fixture()
def stream_client(mock_stream_service: AsyncMock) -> TestClient:
    """TestClient with auth and stream service overrides."""
    app = FastAPI()
    app.include_router(router)

    _error_map: dict[type[Exception], tuple[int, str]] = {
        InvalidInputError: (400, "invalid_input"),
    }
    app.exception_handler(Exception)(make_service_error_handler(_error_map))

    app.dependency_overrides[resolve_identity] = _fake_identity
    app.dependency_overrides[get_event_stream_service] = lambda: mock_stream_service

    return TestClient(app, raise_server_exceptions=False)


def test_last_event_id_forwarded_to_service(
    stream_client: TestClient, mock_stream_service: AsyncMock
) -> None:
    """Last-Event-ID header value is forwarded to the stream service."""
    event_id = "evt_668a1b2c3d4e5f6a7b8c9d0e"
    with stream_client.stream("GET", "/events/stream", headers={"Last-Event-ID": event_id}) as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            break

    mock_stream_service.stream.assert_called_once()
    call_kwargs = mock_stream_service.stream.call_args.kwargs
    assert call_kwargs["last_event_id"] == event_id


def test_last_event_id_takes_precedence_over_since(
    stream_client: TestClient, mock_stream_service: AsyncMock
) -> None:
    """When both Last-Event-ID and since are provided, both are forwarded (service uses cursor)."""
    event_id = "evt_668a1b2c3d4e5f6a7b8c9d0e"
    with stream_client.stream(
        "GET",
        "/events/stream?since=2026-01-01T00:00:00Z",
        headers={"Last-Event-ID": event_id},
    ) as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            break

    call_kwargs = mock_stream_service.stream.call_args.kwargs
    assert call_kwargs["last_event_id"] == event_id
    assert call_kwargs["since"] is not None


def test_last_event_id_invalid_format_returns_400(
    stream_client: TestClient, mock_stream_service: AsyncMock
) -> None:
    """A malformed Last-Event-ID returns 400 without hitting the service."""
    resp = stream_client.get("/events/stream", headers={"Last-Event-ID": "not-a-valid-id"})
    assert resp.status_code == 400
    mock_stream_service.stream.assert_not_called()


def test_last_event_id_none_when_header_absent(
    stream_client: TestClient, mock_stream_service: AsyncMock
) -> None:
    """Without the header, last_event_id is None."""
    with stream_client.stream("GET", "/events/stream") as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            break

    call_kwargs = mock_stream_service.stream.call_args.kwargs
    assert call_kwargs["last_event_id"] is None
