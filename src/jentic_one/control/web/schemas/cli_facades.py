"""Vendor CLI facade list response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CliFacadeResponse(BaseModel):
    """One vendor CLI facade an agent's CLI can be pointed at."""

    vendor: str = Field(
        description="Vendor the facade serves, as passed to `POST /cli-invocations`.",
        examples=["cloudflare"],
    )
    cli: str = Field(
        description="The vendor CLI the facade speaks to, e.g. `cf` for `jentic cf`.",
        examples=["cf"],
    )
    mode: Literal["host", "path"] = Field(
        description=(
            "How the facade is addressed: `host` on a hostname of its own, `path` under a "
            "path prefix on the Broker's own address."
        ),
        examples=["path"],
    )
    address: str | None = Field(
        default=None,
        description=(
            "Base URL to point the CLI at (e.g. `CLOUDFLARE_API_BASE_URL`). Null when the "
            "deployment cannot advertise one: a `remote` backend whose Broker URL is a "
            "loopback address and no `public_address` set for the vendor."
        ),
        examples=["https://broker.jentic.example.com/_facade/cloudflare"],
    )


class CliFacadeListResponse(BaseModel):
    """The vendor CLI facades this deployment serves."""

    data: list[CliFacadeResponse] = Field(
        description="Served facades, one per vendor; empty when none is enabled."
    )
