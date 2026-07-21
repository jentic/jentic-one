"""Inspect domain models — result shapes and load configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel


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


class OperationInspectResult(BaseModel):
    """Full structural detail for a resolved operation."""

    type: Literal["operation"] = "operation"
    operation_id: str
    method: str
    url: str
    name: str | None = None
    description: str | None = None
    api: ApiContext
    parameters: dict[str, object] | None = None
    response_schema: dict[str, object] | None = None
    auth: list[AuthInstruction] | None = None
    server: str | None = None
    raw_spec: dict[str, object] | None = None
    links: InspectLinks
