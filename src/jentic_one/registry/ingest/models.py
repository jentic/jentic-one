"""Ingest domain models — specification identifier and content."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from jentic_one.shared.models import ApiRevisionSourceType


class SpecType(StrEnum):
    """Supported specification types for ingestion."""

    OPENAPI = "openapi"


class ApiIdentifier(BaseModel):
    """Uniquely identifies an API by vendor, name, and version."""

    vendor: str
    name: str
    version: str
    filename: str = "openapi.json"

    @property
    def api_name(self) -> str:
        return f"{self.vendor}/{self.name}"

    def __str__(self) -> str:
        return f"{self.vendor}/{self.name}/{self.version}"


class IngestSpecification(BaseModel):
    """A specification document submitted for ingestion."""

    spec_type: SpecType
    api_identifier: ApiIdentifier
    sha: str | None = None
    metadata: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    source_id: str | None = None
    source_type: ApiRevisionSourceType | None = None
    source_url: str | None = None
    source_filename: str | None = None
    submitted_by: str | None = None
    origin: str | None = None
    # Catalog identity slug (`domain[/sub-api]`) for catalog-originated imports;
    # None for manual/inline sources. Persisted verbatim on the Api row so
    # display surfaces can derive friendly titles without reverse-engineering
    # the slugified vendor/name tuple.
    catalog_api_id: str | None = None

    def to_log_string(self) -> str:
        fields = self.model_dump(exclude={"content"})
        return " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
