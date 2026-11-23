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

    tag: str = Field(description="Tag the operation is grouped under.", examples=["charges"])
    description: str = Field(
        description="The tag's description from the spec.",
        examples=["Charge objects and the money moved by them."],
    )


class ApiContext(BaseModel):
    """Contextual information about the API an operation belongs to.

    ``(vendor, name, version)`` is the canonical API reference — the same
    identity the broker matches credential scopes, revision pins and execution
    records on. ``display_name`` is a human-readable label only and must never
    be used as an identifier.
    """

    vendor: str = Field(
        description="Vendor part of the canonical API reference.", examples=["stripe.com"]
    )
    name: str = Field(description="Name part of the canonical API reference.", examples=["stripe"])
    version: str = Field(
        description="Version part of the canonical API reference.", examples=["2024-04-10"]
    )
    display_name: str | None = Field(
        default=None,
        description="Human-readable label for the API; never an identifier.",
        examples=["Stripe"],
    )
    description: str | None = Field(
        default=None,
        description="The API's description from the spec.",
        examples=["The Stripe REST API."],
    )
    tag_descriptions: list[TagDescription] = Field(
        default=[], description="Descriptions of the tags this operation is grouped under."
    )


class AuthInstruction(BaseModel):
    """Translated security scheme for consumer presentation."""

    type: str = Field(
        description="Security scheme type, e.g. `http`, `apiKey`, `oauth2` or `openIdConnect`.",
        examples=["http"],
    )
    scheme: str | None = Field(
        default=None,
        description="HTTP authentication scheme, for `type=http`.",
        examples=["bearer"],
    )
    in_location: str | None = Field(
        default=None,
        description="Where an API key travels, for `type=apiKey`: `header`, `query` or `cookie`.",
        examples=["header"],
    )
    param_name: str | None = Field(
        default=None,
        description="Name of the header, query or cookie parameter carrying an API key.",
        examples=["Authorization"],
    )
    bearer_format: str | None = Field(
        default=None,
        description="Hint for the bearer token's format, for `scheme=bearer`.",
        examples=["JWT"],
    )
    open_id_connect_url: str | None = Field(
        default=None,
        description="Discovery document URL, for `type=openIdConnect`.",
        examples=["https://accounts.example.com/.well-known/openid-configuration"],
    )
    flows: list[dict[str, object]] | None = Field(
        default=None,
        description="The OAuth 2 flows the scheme declares, for `type=oauth2`.",
    )


class InspectLinks(BaseModel):
    """Hypermedia links for an inspect result."""

    self_link: str = Field(
        description="This inspect result.",
        examples=["https://jentic.example.com/inspect?operation_id=listCharges"],
    )


class OperationParameter(BaseModel):
    """A single declared parameter (query / header / path)."""

    name: str = Field(description="The parameter's name.", examples=["limit"])
    required: bool = Field(
        default=False, description="Whether the operation requires it.", examples=[False]
    )
    description: str | None = Field(
        default=None,
        description="The parameter's description from the spec.",
        examples=["Maximum number of objects to return."],
    )
    schema_: dict[str, object] | None = Field(
        default=None,
        serialization_alias="schema",
        description="The parameter's JSON Schema, as the spec declares it.",
    )


class RequestBodySchema(BaseModel):
    """The request body an operation accepts, projected to a single schema.

    ``content_type`` records which media type the ``schema`` was taken from
    (JSON is preferred when the spec offers several).
    """

    required: bool = Field(
        default=False, description="Whether the operation requires a body.", examples=[True]
    )
    description: str | None = Field(
        default=None,
        description="The body's description from the spec.",
        examples=["The charge to create."],
    )
    content_type: str | None = Field(
        default=None,
        description="Media type the `schema` was taken from.",
        examples=["application/json"],
    )
    schema_: dict[str, object] | None = Field(
        default=None,
        serialization_alias="schema",
        description="The body's JSON Schema, as the spec declares it.",
    )


class OperationInputs(BaseModel):
    """Declared inputs for an operation, grouped by where they belong.

    Declares the query / header / path parameters and request body so a client
    can construct a complete request rather than only supplying path parameters
    (issue #768).
    """

    path: list[OperationParameter] = []
    query: list[OperationParameter] = []
    header: list[OperationParameter] = []
    body: RequestBodySchema | None = None


class OperationInspectResult(BaseModel):
    """Full structural detail for a resolved operation."""

    type: Literal["operation"] = Field(
        default="operation", description="Always `operation`.", examples=["operation"]
    )
    operation_id: str = Field(
        description="The operation's id in its spec.", examples=["listCharges"]
    )
    method: str = Field(description="HTTP method of the operation.", examples=["GET"])
    url: str = Field(
        description="Upstream URL of the operation.",
        examples=["https://api.stripe.com/v1/charges"],
    )
    name: str | None = Field(
        default=None,
        description="The operation's summary from the spec.",
        examples=["List charges"],
    )
    description: str | None = Field(
        default=None,
        description="The operation's description from the spec.",
        examples=["Returns a list of charges."],
    )
    api: ApiContext = Field(description="The API the operation belongs to.")
    inputs: OperationInputs | None = Field(
        default=None, description="Parameters and request body the operation accepts."
    )
    response_schema: dict[str, object] | None = Field(
        default=None, description="JSON Schema of the operation's success response, when known."
    )
    auth: list[AuthInstruction] | None = Field(
        default=None, description="Security schemes the operation accepts."
    )
    server: str | None = Field(
        default=None,
        description="Server URL the operation is resolved against.",
        examples=["https://api.stripe.com"],
    )
    raw_spec: dict[str, object] | None = Field(
        default=None,
        description="The operation's raw spec fragment; only populated for `detail=full`.",
    )
    links: InspectLinks = Field(description="Links for this result.")
