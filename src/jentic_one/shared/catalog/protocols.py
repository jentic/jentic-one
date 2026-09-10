"""Protocols the registry exposes to other surfaces without a direct import."""

from __future__ import annotations

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
