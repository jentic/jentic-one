"""MCP server configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpOAuthConfig(BaseModel):
    """Interactive-OAuth settings for the MCP surface.

    Minimal seam: the full ``server.mcp`` sub-config (``server.mcp.enabled``
    etc.) extends this model in place — fields here must keep their names and
    defaults.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Serve the MCP interactive-OAuth surface: anonymous dynamic client "
            "registration (``POST /oauth-clients``) and the ``/mcp``-scoped OAuth "
            "discovery documents. Off, those routes answer a plain 404."
        ),
    )
    auto_approve_clients: bool = Field(
        default=False,
        description=(
            "Activate dynamically registered OAuth clients immediately. Off, a new "
            "registration lands pending and inactive in the admin queue until an "
            "operator approves it; enable only where self-registered clients are "
            "acceptable without review (e.g. a single-user self-hosted install)."
        ),
    )
    registration_gc_days: int = 90
    """Long-TTL GC policy for never-consented DCR rows: rows are
    GC-eligible only after this many days, and rows with any grant history are
    never GC'd. Policy knob only — no sweep runs yet."""


class McpConfig(BaseModel):
    """``server.mcp`` sub-config."""

    enabled: bool = Field(
        default=False,
        description=(
            "Serve the Streamable HTTP MCP endpoint at ``/mcp``. Off, ``/mcp`` "
            "answers a plain 404, unless ``oauth.enabled`` keeps its 401 + "
            "``WWW-Authenticate`` discovery challenge so OAuth clients can walk "
            "the RFC 9728 chain before the endpoint is turned on."
        ),
    )
    # The broker itself stays MCP-free: the mounted app forwards execute calls to
    # it exactly like the CLI does.
    broker_url: str = Field(
        default="http://127.0.0.1:8100",
        description=(
            "Broker base URL the MCP ``execute`` tools forward calls to, carrying "
            "the caller's bearer token. The default matches the local install "
            "(loopback broker on port 8100); split deployments set the broker "
            "service's URL. Plain ``http://`` is refused for non-loopback hosts."
        ),
    )
    oauth: McpOAuthConfig = Field(default_factory=McpOAuthConfig)
