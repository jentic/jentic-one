"""Protocols the registry exposes to other surfaces without a direct import."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class CatalogAutoImportProtocol(Protocol):
    """Best-effort auto-import of a public-catalog API into the local registry.

    Consumed by ``ConnectSessionService`` when a vendor credential finishes
    connecting: the broker cannot route a request to (say) ``api.github.com``
    unless the GitHub OpenAPI spec is registered, so we import it now to close
    the gap between "credential exists" and "credential is usable".

    Contract:

    * Idempotent — implementations must no-op when the entry is already
      registered (or not present in the manifest).
    * Best-effort — must not raise. Callers treat failures as advisory and
      continue. The credential is still valid; the operator can import the
      API manually via ``POST /catalog/{api_id}:import``.
    * Returns the enqueued job id when a fresh import was queued; ``None``
      when no work was needed or the import could not be started.
    """

    async def ensure_imported(self, *, api_id: str, initiator_actor_id: str) -> str | None: ...

    async def registered_identity(self, *, api_id: str) -> tuple[str, str] | None:
        """Return the ``(vendor, name)`` of the local API imported from ``api_id``, or None.

        A vendor connect stamps the credential with this identity so it covers
        the registered API exactly — the name a catalog import gives depends on
        the manifest (sub segment, or the whole id for a clashing entry) and on
        when the API was first imported. ``None`` when nothing is imported yet
        (or on any lookup error).
        """
        ...

    async def current_version(self, *, api_id: str) -> str | None:
        """Return the imported api's current-revision version, or None if not imported yet.

        Used by ``ConnectSessionService.get_review_data`` to fill in the
        ``api_reference.version`` the SPA needs to hit
        ``/apis/{vendor}/{name}/{version}/operations``. The catalog import runs
        asynchronously, so the SPA polls the review-session endpoint while
        this returns ``None``; once the import lands, the returned version
        becomes non-null and the SPA's ops query can enable.
        """
        ...


@dataclass(frozen=True, slots=True)
class DeclaredSecurityScheme:
    """One security scheme an API revision's spec declares (OpenAPI ``securitySchemes``).

    ``type`` is the raw OpenAPI type (``apiKey``, ``http``, ``oauth2``,
    ``openIdConnect``, ``mutualTLS``). ``http_scheme`` is the lowercased
    ``scheme`` of an ``http`` scheme (``bearer``, ``basic``); ``location`` and
    ``field_name`` are an ``apiKey`` scheme's ``in`` and ``name``. The OAuth
    fields come from the scheme's flows: every scope any flow declares, and the
    authorization-code flow's endpoints when it has one.
    """

    name: str
    type: str
    http_scheme: str | None = None
    location: str | None = None
    field_name: str | None = None
    oauth_scopes: tuple[str, ...] = ()
    authorization_url: str | None = None
    token_url: str | None = None


@dataclass(frozen=True, slots=True)
class ApiProvenance:
    """Where an API's live revision came from — shown to the human who approves a credential.

    ``origin`` is the revision's origin (``catalog`` for a public-catalog
    import, ``None`` or another value for a spec someone submitted);
    ``submitted_by`` is the actor that submitted it and ``source_url`` the URL
    it was fetched from, when known.
    """

    origin: str | None
    catalog_api_id: str | None
    submitted_by: str | None
    source_url: str | None


@dataclass(frozen=True, slots=True)
class ApiSecurityView:
    """An API's live revision as the connect flow needs it.

    ``hosts`` is the sorted origin set (``scheme://host[:port]``) every server
    of the revision can target, with host-position server variables expanded
    over their default and enum values. ``unpinned_host_variables`` names the
    host-position variables that declare no ``enum`` — their value is free, so
    a credential sent to that server could be steered to any host.
    """

    vendor: str
    name: str
    version: str
    display_name: str | None
    revision_id: str
    schemes: tuple[DeclaredSecurityScheme, ...]
    hosts: tuple[str, ...]
    provenance: ApiProvenance
    unpinned_host_variables: tuple[str, ...] = field(default_factory=tuple)


class SecuritySchemesLookupProtocol(Protocol):
    """Read an API's live revision for the connect flow without importing the registry.

    Consumed by ``ConnectSessionService`` for connect sessions that target a
    registry API: the declared schemes decide which credential a human enters,
    and the hosts are pinned for the session's lifetime.

    Contract: returns ``None`` when no API with that identity exists or it has
    no live (current) revision. Lookup failures other than "not found" raise.
    """

    async def lookup(self, *, vendor: str, name: str, version: str) -> ApiSecurityView | None: ...
