"""Actor API response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models import ActorType


class ActorSummaryResponse(BaseModel):
    """Single actor entry in the actors list."""

    id: str
    actor_type: ActorType
    name: str
    active: bool
    created_at: datetime


class ActorListResponse(BaseModel):
    """Paginated list of actors."""

    data: list[ActorSummaryResponse]
    has_more: bool
    next_cursor: str | None = None


class ActorLookupEntryResponse(BaseModel):
    """Display fields of one actor resolved by id."""

    id: str = Field(description="Actor id (`usr_…` or `agnt_…`).")
    actor_type: ActorType = Field(description="Whether the actor is a user or an agent.")
    name: str = Field(description="Display name: a user's full name or an agent's name.")
    active: bool = Field(description="False for a disabled user or a non-active agent.")


class ActorLookupResponse(BaseModel):
    """Actors resolved by id; ids that match no actor are omitted."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "data": [
                        {
                            "id": "usr_2abc",
                            "actor_type": "user",
                            "name": "Ada Lovelace",
                            "active": True,
                        }
                    ]
                }
            ]
        }
    )

    data: list[ActorLookupEntryResponse]
