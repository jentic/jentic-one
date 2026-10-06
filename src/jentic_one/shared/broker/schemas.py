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

from pydantic import BaseModel, ConfigDict

from jentic_one.shared.schemas import OperationInfo


class ExecuteRequestContext(BaseModel):
    """Contextual metadata for a broker proxy request — discovery-driven.

    ``toolkit_id`` is nullable-legacy: nothing sets it since theme-5 Phase 6b
    deleted toolkit derivation; it stays so queued/in-process callers built
    against the older shape keep validating. ``operation`` / ``api_*`` come
    from in-process discovery, not inbound ``Jentic-Api-*`` headers.
    """

    # Forbid unknown fields: this is part of the public Broker contract and
    # pydantic's default extra="ignore" would silently DROP a misspelled or
    # unsupported kwarg (e.g. a flat ``operation_id=`` — the identity rides
    # ``operation``) instead of failing loudly at the caller.
    model_config = ConfigDict(extra="forbid")

    upstream_url: str
    method: str
    trace_id: str
    toolkit_id: str | None = None
    # The discovered operation (id + path template + method), carried as one
    # object so every persistence/telemetry seam sees the same identity.
    operation: OperationInfo | None = None
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
    # OpenAPI server-variable values resolved from the request URL by discovery
    # (credential selection is scoped on them), and the declared defaults of
    # variables the URL left as a ``{name}`` placeholder (substituted when the
    # credential supplies no value).
    server_variables: dict[str, str] | None = None
    server_variable_defaults: dict[str, str] | None = None
    # Discovery could not determine the URL's server-variable values (an index
    # row that predates server-variable capture): credentials scoped by
    # ``server_variables`` are then not injected.
    server_variables_unresolved: bool = False
    # Attribution for the stored credential the resolver picked, once injection
    # has run (#740). ``None`` before injection, when no credential path exists,
    # or when the request used inline auth. Carried on the context so both the
    # sync router and the async worker can stamp ``Jentic-Credential-*`` response
    # headers, persist the ids on the execution record, and correlate the
    # ``CREDENTIAL_ACCESSED`` audit event to this execution.
    credential_id: str | None = None
    credential_name: str | None = None

    @property
    def operation_id(self) -> str | None:
        """The discovered operation's opaque registry id, or ``None``.

        Read-only convenience for telemetry/header seams; the identity itself
        is set through ``operation`` (an ``operation_id=`` kwarg is rejected by
        ``extra="forbid"``).
        """
        return self.operation.id if self.operation else None
