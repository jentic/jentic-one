"""Vendor CLI facade list response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jentic_one.shared.cli_facades import FacadeProtocol


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
    protocols: list[FacadeProtocol] = Field(
        description=(
            "What the vendor's tools reach through Jentic: `rest` (the vendor's REST API), "
            "`graphql` (GitHub's GraphQL endpoint, governed by GraphQL rules) and `git` "
            "(git clone, fetch and push to github.com, governed by git rules; listed only "
            "while the governed git pipeline is on)."
        ),
        examples=[["rest"]],
    )
    display_name: str | None = Field(
        default=None,
        description="How the vendor is named to people, e.g. in a wrapper's directives.",
        examples=["Cloudflare"],
    )
    binary: str | None = Field(
        default=None,
        description="The vendor CLI's executable, looked up on PATH.",
        examples=["cf"],
    )
    supported_range: str | None = Field(
        default=None,
        description="CLI versions the vendor's CLI profile is written for, e.g. `>=2.40.0,<3`.",
        examples=[">=2.40.0,<3"],
    )
    wrapper: CliFacadeWrapperWiringResponse | None = Field(
        default=None,
        description=(
            "How `jentic <cli>` wires the vendor CLI (from its CLI profile); null when the CLI "
            "is not wrapped."
        ),
    )
    container: CliFacadeContainerWiringResponse | None = Field(
        default=None,
        description="What `jentic run --container` sets for the vendor; null when nothing.",
    )
    governed_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Domains whose hosts the egress proxy of `jentic run --container` relays to the "
            "Broker for this vendor."
        ),
        examples=[["api.cloudflare.com"]],
    )


class CliFacadeWrapperWiringResponse(BaseModel):
    """How `jentic <cli>` wires the vendor CLI on the agent's machine."""

    env_set: dict[str, str] = Field(
        description=(
            "Variables to set for the CLI. A value is a literal or a placeholder the wrapper "
            "fills in: `{address}` (the facade address), `{address_host}` (its hostname) or "
            "`{token}` (the command's invocation token)."
        ),
        examples=[{"CLOUDFLARE_API_BASE_URL": "{address}", "CLOUDFLARE_API_TOKEN": "{token}"}],
    )
    env_strip: list[str] = Field(
        description=(
            "Credential variables the CLI could authenticate with instead of the invocation "
            "token; removed from its environment."
        ),
        examples=[["CLOUDFLARE_API_KEY", "CF_API_TOKEN"]],
    )


class CliFacadeContainerWiringResponse(BaseModel):
    """What `jentic run --container` sets for the vendor's tools."""

    env_set: dict[str, str] = Field(
        description=(
            "Variables to set on the agent container. A value is a literal or `{placeholder}`, "
            "the egress proxy's fixed non-secret placeholder token."
        ),
        examples=[{"GH_TOKEN": "{placeholder}", "GIT_TERMINAL_PROMPT": "0"}],
    )
    env_strip: list[str] = Field(
        description="Variables removed from the agent container's environment.",
        examples=[["GITHUB_TOKEN"]],
    )
    tunnel_hosts: list[str] = Field(
        description=(
            "Hosts the vendor's tools need reached directly (tunnelled by the egress proxy, "
            "never decrypted)."
        ),
        examples=[[]],
    )


class CliFacadeListResponse(BaseModel):
    """The vendor CLI facades this deployment serves."""

    data: list[CliFacadeResponse] = Field(
        description="Served facades, one per vendor; empty when none is enabled."
    )
