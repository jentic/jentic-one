"""Request/response schemas for the Notes endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from jentic_one.shared.web import openapi_examples as ex

_EX_NOTE_BODY = (
    "Stripe returns 401 unless the secret key is sent as a Bearer token; "
    "Basic auth with an empty password also works."
)


class NoteType(StrEnum):
    """Category of a note (matches the spec ``NoteType`` enum)."""

    auth_quirk = "auth_quirk"
    usage_hint = "usage_hint"
    execution_feedback = "execution_feedback"
    correction = "correction"


class NoteConfidence(StrEnum):
    """Author-supplied confidence in a note (matches the spec ``NoteConfidence`` enum)."""

    observed = "observed"
    suspected = "suspected"
    verified = "verified"


class NoteSource(StrEnum):
    """Origin tag for a note (matches the spec ``NoteSource`` enum)."""

    agent = "agent"
    human = "human"
    platform = "platform"


class NoteApiReference(BaseModel):
    """Loose ``(vendor, name, version)`` identity tuple for a registered API."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(description="Vendor of the API.", examples=[ex.API_VENDOR])
    name: str = Field(description="Name of the API within the vendor.", examples=[ex.API_NAME])
    version: str = Field(description="Version of the API.", examples=[ex.API_VERSION])


class NoteResource(BaseModel):
    """Exactly-one-of resource identifier for a note (request side)."""

    model_config = ConfigDict(extra="forbid")

    api: NoteApiReference | None = Field(
        default=None, description="API to attach the note to; set exactly one resource field."
    )
    operation_id: str | None = Field(
        default=None,
        description="ID of the operation to attach the note to; set exactly one resource field.",
        examples=[ex.REGISTRY_OPERATION_ID],
    )
    execution_id: str | None = Field(
        default=None,
        description="ID of the execution to attach the note to; set exactly one resource field.",
        examples=[ex.EXECUTION_ID],
    )
    credential_id: str | None = Field(
        default=None,
        description="ID of the credential to attach the note to; set exactly one resource field.",
        examples=[ex.CREDENTIAL_ID],
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> NoteResource:
        populated = sum(
            1
            for v in [self.api, self.operation_id, self.execution_id, self.credential_id]
            if v is not None
        )
        if populated != 1:
            msg = "Exactly one resource field must be provided"
            raise ValueError(msg)
        return self


class NoteCreateRequest(BaseModel):
    """Payload for creating a new note."""

    model_config = ConfigDict(extra="forbid")

    resource: NoteResource
    type: NoteType | None = Field(
        default=None,
        description="Category of the note; null if uncategorised.",
        examples=[NoteType.auth_quirk],
    )
    body: str = Field(
        max_length=4000,
        description="Text of the note, up to 4000 characters.",
        examples=[_EX_NOTE_BODY],
    )
    confidence: NoteConfidence | None = Field(
        default=None,
        description="Author's confidence in the note; null if not stated.",
        examples=[NoteConfidence.observed],
    )
    source: NoteSource | None = Field(
        default=None,
        description="Origin of the note (`agent`, `human` or `platform`); null if not stated.",
        examples=[NoteSource.agent],
    )
    related_execution_id: str | None = Field(
        default=None,
        description="ID of an execution the note relates to; null if none.",
        examples=[ex.EXECUTION_ID],
    )


class NoteUpdateRequest(BaseModel):
    """Payload for updating an existing note (partial)."""

    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(
        default=None,
        max_length=4000,
        description="New text of the note, up to 4000 characters; may be omitted but not null.",
        examples=[_EX_NOTE_BODY],
    )
    type: NoteType | None = Field(
        default=None,
        description="New category of the note; null clears it.",
        examples=[NoteType.auth_quirk],
    )
    confidence: NoteConfidence | None = Field(
        default=None,
        description="New confidence in the note; null clears it.",
        examples=[NoteConfidence.verified],
    )
    source: NoteSource | None = Field(
        default=None,
        description="New origin of the note; null clears it.",
        examples=[NoteSource.agent],
    )
    related_execution_id: str | None = Field(
        default=None,
        description="ID of an execution the note relates to; null clears it.",
        examples=[ex.EXECUTION_ID],
    )

    @model_validator(mode="after")
    def _body_not_null(self) -> NoteUpdateRequest:
        if "body" in self.model_fields_set and self.body is None:
            msg = "body may not be null"
            raise ValueError(msg)
        return self


class NoteResourceResponse(BaseModel):
    """Polymorphic reference to the resource a note is attached to (response side)."""

    api: NoteApiReference | None = None
    operation_id: str | None = None
    execution_id: str | None = None
    credential_id: str | None = None

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        """Emit only the single populated resource field, matching the spec shape."""
        if self.api is not None:
            return {"api": self.api.model_dump()}
        if self.operation_id is not None:
            return {"operation_id": self.operation_id}
        if self.execution_id is not None:
            return {"execution_id": self.execution_id}
        if self.credential_id is not None:
            return {"credential_id": self.credential_id}
        return {}


class NoteLinksResponse(BaseModel):
    """Hypermedia links for a note resource."""

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(
        serialization_alias="self", examples=[f"{ex.BASE_URL}/notes/{ex.NOTE_ID}"]
    )
    resource: str | None = Field(
        default=None,
        examples=[f"{ex.BASE_URL}/inspect?operation_id={ex.REGISTRY_OPERATION_ID}"],
    )


class NoteResponse(BaseModel):
    """Full note resource response."""

    model_config = ConfigDict(populate_by_name=True)

    note_id: str = Field(examples=[ex.NOTE_ID])
    # ``NoteResourceResponse._serialize`` emits one resource field, so the model
    # serialises to a bare object with no properties the example composer can
    # read; the example for it has to be written here.
    resource: NoteResourceResponse = Field(examples=[{"operation_id": ex.REGISTRY_OPERATION_ID}])
    type: NoteType | None = Field(examples=[NoteType.auth_quirk])
    body: str = Field(examples=[_EX_NOTE_BODY])
    confidence: NoteConfidence | None = Field(examples=[NoteConfidence.observed])
    confidence_source: str = Field(examples=["client"])
    source: NoteSource | None = Field(examples=[NoteSource.agent])
    created_by: str = Field(examples=[ex.AGENT_ID])
    related_execution_id: str | None = Field(examples=[ex.EXECUTION_ID])
    revision: int = Field(examples=[3])
    created_at: datetime = Field(examples=["2026-01-15T09:30:00Z"])
    updated_at: datetime = Field(examples=["2026-01-20T14:05:00Z"])
    links: NoteLinksResponse = Field(serialization_alias="_links")


class NoteListResponse(BaseModel):
    """Cursor-paginated list of notes."""

    data: list[NoteResponse]
    has_more: bool = Field(examples=[True])
    next_cursor: str | None = Field(default=None, examples=[ex.CURSOR])
