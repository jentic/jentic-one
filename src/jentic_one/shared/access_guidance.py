"""Canonical access-recovery guidance for the broker's missing-binding path.

An API is only "served" once a credential covering it is provisioned; only
then can an agent be bound to it. When nothing serves an API yet, the broker's
missing-binding recovery directive must recommend provisioning a credential as
the first step (see issue #683).

The module also holds the reverse lookup that decides whether the provisioning
step is agent-initiable (``jentic connect <vendor>`` / the
``request_connection`` MCP tool) or operator-only: an API is connectable when a
vendor-registry config entry or an active shared OAuth-app registration covers
it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jentic_one.shared.broker.protocols import ConnectableRegistration
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
    """Whether the API identity is the one a catalog import of ``catalog_api_id`` registers."""
    return any(
        credential_covers(scope, vendor=vendor, name=name, version=version)
        for scope in catalog_api_scopes(catalog_api_id)
    )


@dataclass(frozen=True, slots=True)
class ConnectTarget:
    """What an agent passes to the connect surface to provision a denied API.

    ``vendor_key`` is the key ``POST /integrations:connect`` takes.
    ``registration_id`` pins the one shared OAuth-app registration that
    covers the API; it is set only when exactly one registration matches.
    """

    vendor_key: str
    registration_id: str | None = None

    def cli_command(self) -> str:
        """The ``jentic connect`` command for this target, pinned when one app matched.

        The pin matters when the key is shared by registrations for other APIs
        (e.g. one ``google`` key for Gmail and Drive apps): unpinned, that
        connect would be refused as ``ambiguous_vendor``.
        """
        command = f"jentic connect {self.vendor_key}"
        if self.registration_id is not None:
            command += f" --registration {self.registration_id}"
        return command

    def as_parameters(self) -> dict[str, Any]:
        """The ``parameters.connect`` object a broker directive carries."""
        params: dict[str, Any] = {"vendor_key": self.vendor_key}
        if self.registration_id is not None:
            params["registration_id"] = self.registration_id
        return params


def connect_target(
    vendors: VendorRegistryConfig,
    registrations: Iterable[ConnectableRegistration],
    *,
    vendor: str,
    name: str,
    version: str = "",
) -> ConnectTarget | None:
    """Resolve the connect target covering an API identity, if any.

    A covering vendor-registry config entry wins: an unpinned
    ``:connect <key>`` resolves to the config entry before any registration
    sharing the key. Otherwise the active shared OAuth-app registrations are
    matched by their ``catalog_api_id`` on the same coverage rule:

    - exactly one covers the API → its ``api_vendor`` plus its id;
    - several cover it → the first ``api_vendor`` in sorted order, with no
      ``registration_id`` (choosing among shared apps is operator policy, so
      the connect surface answers ``ambiguous_vendor`` and the agent asks);
    - none → ``None``.
    """
    key = connect_vendor_key(vendors, vendor=vendor, name=name, version=version)
    if key is not None:
        return ConnectTarget(vendor_key=key)
    covering = sorted(
        (
            r
            for r in registrations
            if catalog_api_id_covers(r.catalog_api_id, vendor=vendor, name=name, version=version)
        ),
        key=lambda r: (r.api_vendor, r.id),
    )
    if not covering:
        return None
    if len(covering) == 1:
        return ConnectTarget(vendor_key=covering[0].api_vendor, registration_id=covering[0].id)
    return ConnectTarget(vendor_key=covering[0].api_vendor)
