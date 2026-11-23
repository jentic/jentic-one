"""Outbound-HTTP helpers shared by the broker, the async job worker and MCP.

``shared/jobs/`` and ``mcp/`` cannot import ``broker/`` (arch boundary), so the
request and response handling they must agree on with the broker lives here.
"""
