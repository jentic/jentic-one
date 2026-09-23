"""Canonical access-recovery guidance for the broker's missing-binding path.

An API is only "served" once a credential covering it is provisioned; only
then can an agent be bound to it. When nothing serves an API yet, the broker's
missing-binding recovery directive must recommend provisioning a credential as
the first step (see issue #683).

Historically this wording was shared with the control access-request approval
flow so a denial reason could never contradict the broker directive; theme 7
removed that flow, leaving the broker as the sole consumer. It stays under
``shared`` as the single wording module pending a possible fold into
``broker/core/exceptions.py`` (theme-7 open question). It also holds the
vendor-registry reverse lookup that decides whether the provisioning step is
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
    """The canonical reason when no credential serves ``api`` yet.

    ``api`` is a ``vendor[/name][@version]`` label. The recommended first
    step — provision a credential — matches the broker's missing-binding
    directives (see issue #683). Phrased as a statement of the condition plus
    the recommended first step, matching the broker directive. For a registry
    vendor the agent can start that step itself (``jentic connect <vendor>`` /
    the ``request_connection`` MCP tool); approval stays human either way.
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
