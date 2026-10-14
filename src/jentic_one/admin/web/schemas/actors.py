"""Actor API response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models import ActorType
from jentic_one.shared.web import openapi_examples as ex


class ActorSummaryResponse(BaseModel):
    """Single actor entry in the actors list."""

    id: str = Field(description="Actor id (`usr_…` or `agnt_…`).", examples=[ex.USER_ID])
    actor_type: ActorType = Field(examples=["user"])
    name: str = Field(
        description="Display name: a user's full name or an agent's name.",
        examples=["Ada Lovelace"],
    )
    active: bool = Field(
        description="False for a disabled user or a non-active agent.", examples=[True]
    )
    created_at: datetime = Field(
        description="When the actor was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )


class ActorListResponse(BaseModel):
    """Paginated list of actors."""

    data: list[ActorSummaryResponse] = Field(description="The page of actors.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )


class ActorLookupEntryResponse(BaseModel):
    """Display fields of one actor resolved by id."""

    id: str = Field(description="Actor id (`usr_…` or `agnt_…`).", examples=[ex.USER_ID])
    actor_type: ActorType = Field(
        description="Whether the actor is a user or an agent.", examples=["user"]
    )
    name: str = Field(
        description="Display name: a user's full name or an agent's name.",
        examples=["Ada Lovelace"],
    )
    active: bool = Field(
        description="False for a disabled user or a non-active agent.", examples=[True]
    )


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

    data: list[ActorLookupEntryResponse] = Field(
        description="The resolved actors; ids that match no visible actor are omitted."
    )
