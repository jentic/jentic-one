"""MCP server configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpOAuthConfig(BaseModel):
    """Interactive-OAuth settings for the MCP surface.

    Minimal seam: the full ``server.mcp`` sub-config (``server.mcp.enabled``
    etc.) extends this model in place — fields here must keep their names and
    defaults.
    """

    enabled: bool = False
    """Master switch for the interactive-OAuth surface. Off (the default) the
    anonymous DCR front door ``POST /oauth-clients`` returns a plain 404,
    indistinguishable from not-shipped."""
    auto_approve_clients: bool = False
    """D9 (amended): auto-approve DCR registrations (``approval_status='approved'``
    + ``active=true`` at registration). Default **false** everywhere —
    approval-first posture: new registrations land ``pending`` + inactive in
    the admin queue until an operator approves them. Setting this ``true`` is
    an explicit opt-in for deployments that accept self-registered clients
    without review (e.g. self-hosted single-user installs)."""
    registration_gc_days: int = 90
    """Long-TTL GC policy for never-consented DCR rows: rows are
    GC-eligible only after this many days, and rows with any grant history are
    never GC'd. Policy knob only — no sweep runs yet."""


class McpConfig(BaseModel):
    """``server.mcp`` sub-config."""

    enabled: bool = False
    """Master switch for the daemon-native Streamable HTTP ``/mcp`` endpoint.
    Off (the default) the mounted app answers the framework's
    plain route-not-found 404 — unless ``oauth.enabled`` keeps the
    discovery-challenge behaviour alive (401 + ``WWW-Authenticate``) so OAuth
    clients can still walk the RFC 9728 chain ahead of the endpoint being
    turned on. Flipping this on is an explicit operator choice."""
    broker_url: str = "http://127.0.0.1:8100"
    """Broker base URL for the MCP ``execute`` tools' server-side
    control-plane→broker proxy hop (the broker stays MCP-free,
    so the mounted app forwards execute calls to the broker exactly like the
    CLI does). The default matches the local install topology (plain-HTTP
    loopback broker on 8100); compose/split deployments set the broker
    service's URL. Plaintext ``http://`` is refused for non-loopback hosts —
    the caller's bearer rides this hop (SEC-1 posture)."""
    oauth: McpOAuthConfig = Field(default_factory=McpOAuthConfig)
