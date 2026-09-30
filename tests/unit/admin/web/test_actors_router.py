"""Unit tests for the actors router response serialization."""

from __future__ import annotations

from datetime import UTC, datetime

from jentic_one.admin.services.schemas.actors import ActorView
from jentic_one.admin.web.schemas.actors import ActorListResponse, ActorSummaryResponse
from jentic_one.shared.models import ActorType


def _make_view(**overrides: object) -> ActorView:
    defaults: dict[str, object] = {
        "id": "usr_abc",
        "actor_type": ActorType.USER,
        "name": "Alice Smith",
        "active": True,
        "created_at": datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return ActorView(**defaults)  # type: ignore[arg-type]


def test_actor_summary_response_fields() -> None:
    view = _make_view(id="agnt_001", actor_type=ActorType.AGENT, name="Bot-1", active=False)
    resp = ActorSummaryResponse(
        id=view.id,
        actor_type=view.actor_type,
        name=view.name,
        active=view.active,
        created_at=view.created_at,
    )
    data = resp.model_dump()
    assert data["id"] == "agnt_001"
    assert data["actor_type"] == "agent"
    assert data["name"] == "Bot-1"
    assert data["active"] is False
    assert data["created_at"] == datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def test_actor_list_response_pagination() -> None:
    views = [_make_view(), _make_view(id="agnt_002", actor_type=ActorType.AGENT, name="Bot-2")]
    resp = ActorListResponse(
        data=[
            ActorSummaryResponse(
                id=v.id,
                actor_type=v.actor_type,
                name=v.name,
                active=v.active,
                created_at=v.created_at,
            )
            for v in views
        ],
        has_more=True,
        next_cursor="abc123",
    )
    assert len(resp.data) == 2
    assert resp.has_more is True
    assert resp.next_cursor == "abc123"


def test_actor_type_enum_values() -> None:
    for actor_type, label in [
        (ActorType.USER, "user"),
        (ActorType.AGENT, "agent"),
    ]:
        view = _make_view(actor_type=actor_type)
        resp = ActorSummaryResponse(
            id=view.id,
            actor_type=view.actor_type,
            name=view.name,
            active=view.active,
            created_at=view.created_at,
        )
        assert resp.model_dump()["actor_type"] == label
