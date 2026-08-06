"""Inspect domain models — result shapes and load configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class InspectLoadOptions:
    """Controls which optional data is loaded during inspection."""

    load_spec: bool
    load_auth: bool
    load_server: bool


SUMMARY_LOAD_OPTIONS = InspectLoadOptions(load_spec=False, load_auth=True, load_server=True)


class TagDescription(BaseModel):
    """A tag name paired with its description from the spec."""

    tag: str
    description: str


class ApiContext(BaseModel):
    """Contextual information about the API an operation belongs to."""

    vendor: str
    name: str
    version: str
    description: str | None = None
    tag_descriptions: list[TagDescription] = []


class AuthInstruction(BaseModel):
    """Translated security scheme for consumer presentation."""

    type: str
    scheme: str | None = None
    in_location: str | None = None
    param_name: str | None = None
    bearer_format: str | None = None
    open_id_connect_url: str | None = None
    flows: list[dict[str, object]] | None = None


class InspectLinks(BaseModel):
    """Hypermedia links for an inspect result."""

    self_link: str


class OperationParameter(BaseModel):
    """A single declared parameter (query / header / path)."""

    name: str
    required: bool = False
    description: str | None = None
    schema_: dict[str, object] | None = Field(default=None, serialization_alias="schema")


class RequestBodySchema(BaseModel):
    """The request body an operation accepts, projected to a single schema.

    ``content_type`` records which media type the ``schema`` was taken from
    (JSON is preferred when the spec offers several).
    """

    required: bool = False
    description: str | None = None
    content_type: str | None = None
    schema_: dict[str, object] | None = Field(default=None, serialization_alias="schema")


class OperationInputs(BaseModel):
    """Declared inputs for an operation, grouped by where they belong.

    Restores the query / header / path parameters and request body that spec
    import used to drop, so a client can construct a complete request rather
    than only supplying path parameters (issue #768).
    """

    path: list[OperationParameter] = []
    query: list[OperationParameter] = []
    header: list[OperationParameter] = []
    body: RequestBodySchema | None = None


class OperationInspectResult(BaseModel):
    """Full structural detail for a resolved operation."""

    type: Literal["operation"] = "operation"
    operation_id: str
    method: str
    url: str
    name: str | None = None
    description: str | None = None
    api: ApiContext
    inputs: OperationInputs | None = None
    response_schema: dict[str, object] | None = None
    auth: list[AuthInstruction] | None = None
    server: str | None = None
    raw_spec: dict[str, object] | None = None
    links: InspectLinks
