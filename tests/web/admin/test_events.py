"""Web tests for the admin events router."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import EventView, Heartbeat
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


def test_list_success(authed_client: TestClient) -> None:
    resp = authed_client.get("/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data


def test_list_with_filters(authed_client: TestClient) -> None:
    resp = authed_client.get("/events?severity=warning&requires_action=false")
    assert resp.status_code == 200


def test_list_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/events")
    assert resp.status_code == 401


def test_get_not_found(authed_client: TestClient) -> None:
    resp = authed_client.get("/events/nonexistent-event-id")
    assert resp.status_code == 404
    assert resp.json()["type"] == "event_not_found"


@pytest.fixture()
async def stored_retired_event_id(web_context: Context) -> AsyncGenerator[str, None]:
    """Seed a stored ``access_request.filed`` row, as an install upgrading past
    theme 7 would have — the kind is retired from ``EventType`` but rows written
    before the removal survive in the events table."""
    async with web_context.admin_db.session() as session:
        event = await EventRepository.create(
            session,
            type="access_request.filed",
            severity="info",
            summary="Access request areq_historic filed",
            requires_action=True,
            data={"access_request_id": "areq_historic"},
            created_by="agt_historic",
            actor_id="agt_historic",
            actor_type="agent",
        )
        await session.commit()
        event_id = event.id
    yield event_id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(Event).where(Event.id == event_id))
        await session.commit()


def test_stored_retired_kind_lists_and_projects(
    authed_client: TestClient, stored_retired_event_id: str
) -> None:
    """A stored retired ``access_request.*`` row must list and fetch without
    crashing (theme 7 read tolerance), with no action link to the removed UI."""
    resp = authed_client.get(f"/events/{stored_retired_event_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "access_request.filed"
    # No action link to the removed UI (omitted from the payload when None).
    assert body.get("action") is None

    listed = authed_client.get("/events?requires_action=true")
    assert listed.status_code == 200
    ids = [item["event_id"] for item in listed.json()["data"]]
    assert stored_retired_event_id in ids


async def test_stream_yields_heartbeat_when_idle(web_context: Context) -> None:
    # The SSE endpoint streams forever, which a blocking HTTP TestClient cannot
    # drive without hanging. Exercise the streaming source directly instead:
    # with no events, the first item must be a heartbeat. A short poll interval
    # keeps the test fast and we stop after the first item.
    svc = EventStreamService(web_context)
    stream = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        svc.stream(poll_interval_seconds=0.01),
    )
    try:
        first = await stream.__anext__()
    finally:
        await stream.aclose()
    assert isinstance(first, Heartbeat)
