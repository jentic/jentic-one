"""Canonical access-recovery guidance shared across broker and control.

An API is only "served" once a credential covering it is provisioned; only
then can an agent be bound to it. When nothing serves an API yet, the broker's
missing-binding recovery directive and the control approval-denial reason must
recommend the *same* first step — provision a credential — instead of
contradicting each other (see issue #683).

This module holds the single wording both layers reference so the two messages
can never drift. It lives under ``shared`` because both the public broker
(``broker/core/exceptions.py``) and control (``control/services/access_requests``)
may import ``shared`` but not each other. It also holds the vendor-registry
reverse lookup both layers use to decide whether the provisioning step is
agent-initiable (``jentic connect <vendor>`` / the ``request_connection`` MCP
tool — theme-7 Phase 1b) or operator-only.
"""

from __future__ import annotations

from jentic_one.shared.config import VendorRegistryConfig
from jentic_one.shared.models.api_identity import (
    canonical_credential_scope,
    credential_covers,
)


def no_credential_serves_api_reason(api: str) -> str:
    """The canonical denial reason when no credential serves ``api`` yet.

    ``api`` is a ``vendor[/name][@version]`` label. A ``credential:bind`` filed
    by API reference can only resolve once a credential covering that API
    exists and is visible to the approver. The recommended first step —
    provision a credential — is the same one the broker's missing-binding
    directives name, so the two layers never contradict each other (see issue
    #683). Phrased as a statement of the condition plus the recommended first
    step, matching the broker directive. For a registry vendor the agent can
    start that step itself (``jentic connect <vendor>`` / the
    ``request_connection`` MCP tool); approval stays human either way.
    """
    return (
        f"No credential covers API {api}; provision a credential for it first "
        "(the agent can start a connect session for a registry vendor, or an "
        "operator connects it via POST /credentials), then approve the "
        "credential binding"
    )


def connect_vendor_key(
    vendors: VendorRegistryConfig, *, vendor: str, name: str, version: str = ""
) -> str | None:
    """Reverse-map an API identity onto its vendor-registry key, if any.

    The connect surface (``POST /integrations:connect``, ``jentic connect``,
    the ``request_connection`` MCP tool) takes the registry *key* (e.g.
    ``github``), while broker directives know the resolved API identity axes
    (e.g. ``github-com`` / ``github-com-api-github-com``). Each registry
    entry's ``VendorAuthConfig.vendor`` holds the catalog api_id
    (``github.com/api.github.com``); decomposing it exactly like the
    connect-session service does at credential-create time
    (``canonical_credential_scope`` over ``domain`` / ``api_id``) yields the
    same identity a registered operation resolves to, so ``credential_covers``
    decides coverage on the same footing the broker itself uses.

    Returns the registry key when exactly the identity is covered by a
    registry entry, else ``None`` — callers gate ``suggested_command`` /
    agent-initiable prose on the lookup rather than suggesting a connect that
    cannot work.
    """
    # First match wins, deterministically: dict iteration preserves the
    # registry's insertion (config) order, so overlapping entries resolve
    # to the earliest-declared key.
    for key, entry in vendors.entries.items():
        raw_vendor = entry.vendor.split("/", 1)[0]
        scope = canonical_credential_scope(vendor=raw_vendor, name=entry.vendor, version=None)
        if credential_covers(scope, vendor=vendor, name=name, version=version):
            return key
    return None
