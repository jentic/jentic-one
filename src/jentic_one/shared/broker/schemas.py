"""Shared broker request-context schema.

``ExecuteRequestContext`` is part of the public :class:`~jentic_one.shared.broker.broker.Broker`
contract (the streaming entry point threads it through), so it lives in
``shared/broker`` — not ``broker/`` — allowing both the broker surface and any
downstream implementation to depend on the same type without ``shared``
importing ``broker`` (forbidden by ``tests/arch/test_module_boundaries.py``).

``broker/core/schemas.py`` re-exports this name, so existing broker-internal call
sites keep importing it from their old module unchanged; this module is the
single definition.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExecuteRequestContext(BaseModel):
    """Contextual metadata for a broker proxy request — discovery-driven.

    ``toolkit_id`` is no longer a required inbound header: it is derived (§03) or
    supplied as an inbound disambiguator. ``operation_id`` / ``api_*`` come from
    in-process discovery, not inbound ``Jentic-Api-*`` headers.
    """

    upstream_url: str
    method: str
    trace_id: str
    toolkit_id: str | None = None
    operation_id: str | None = None
    api_vendor: str | None = None
    api_name: str | None = None
    api_version: str | None = None
    prefer: str | None = None
    pinned_revisions: dict[str, Any] | None = None
    # True when the discovered API's spec uses a templated host / server variable
    # (e.g. ``https://{region}.posthog.com``). Drives the region-mismatch hint the
    # broker attaches to an upstream 401/403 (#638) so a valid key hitting the
    # wrong host is not a dead-end "Invalid Key".
    has_server_variable: bool = False
