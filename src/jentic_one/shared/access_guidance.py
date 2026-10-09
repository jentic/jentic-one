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
    CredentialScope,
    canonical_credential_scope,
    credential_covers,
)
from jentic_one.shared.vendor_domain import vendor_from_api_id


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
    (e.g. ``github-com`` / ``api-github-com``). Each registry entry's
    ``VendorAuthConfig.vendor`` holds the catalog api_id
    (``github.com/api.github.com``); ``catalog_api_id_covers`` matches it
    against every identity a catalog import of that id can register under, so
    coverage is decided on the same footing the broker itself uses.

    Returns the registry key when exactly the identity is covered by a
    registry entry, else ``None`` — callers gate ``suggested_command`` /
    agent-initiable prose on the lookup rather than suggesting a connect that
    cannot work.
    """
    # First match wins, deterministically: dict iteration preserves the
    # registry's insertion (config) order, so overlapping entries resolve
    # to the earliest-declared key.
    for key, entry in vendors.entries.items():
        if catalog_api_id_covers(entry.vendor, vendor=vendor, name=name, version=version):
            return key
    return None


def catalog_import_name(catalog_api_id: str) -> str:
    """The ``api_name`` a first catalog import of ``catalog_api_id`` seeds.

    A ``domain/sub`` id seeds its sub segment (``github.com/api.github.com`` →
    ``api.github.com``); a bare-domain id seeds the whole id. This is the
    registry's derivation for an entry whose sub segment clashes with no other
    manifest entry; a clashing entry (and an API imported before the sub-segment
    naming) carries the whole id instead.
    """
    _, sep, sub = catalog_api_id.partition("/")
    return sub if sep and sub else catalog_api_id


def catalog_api_scopes(catalog_api_id: str) -> tuple[CredentialScope, ...]:
    """Every API scope an import of ``catalog_api_id`` can be registered under.

    The vendor axis is the registrable domain of the api_id's host; the name
    axis is either the sub segment (a regular catalog import) or the whole id
    (a clashing entry, or an API imported before the sub-segment naming). The
    version is unscoped.
    """
    raw_vendor = vendor_from_api_id(catalog_api_id) or catalog_api_id
    names = dict.fromkeys((catalog_import_name(catalog_api_id), catalog_api_id))
    return tuple(canonical_credential_scope(vendor=raw_vendor, name=n, version=None) for n in names)


def catalog_api_id_covers(catalog_api_id: str, *, vendor: str, name: str, version: str) -> bool:
    """Whether the API identity is one a catalog import of ``catalog_api_id`` registers."""
    return any(
        credential_covers(scope, vendor=vendor, name=name, version=version)
        for scope in catalog_api_scopes(catalog_api_id)
    )
