"""Service-layer views for the vendor auth registry.

Two shapes live here:

* ``ResolvedScope`` — a value object returned by ``merge_scopes`` describing a
  vendor's scope catalog with per-scope ``default`` / ``requested`` flags.
* ``VendorEntry`` — the unified read view returned by the DB-first + config
  fallback resolution path. It surfaces both admin-registered
  ``oauth_app_registrations`` rows and platform-shipped ``VendorAuthConfig``
  entries through a single shape. The ``source`` field records which side of
  the seam the row came from so callers can nudge behaviour if needed
  (e.g. show an admin-managed badge in the UI).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

VendorEntrySource = Literal["db", "config"]
VendorFlowKind = Literal["authorization_code", "device_authorization"]


@dataclass(slots=True, frozen=True)
class ResolvedScope:
    """A scope resolved for a specific connect request.

    ``default`` = pre-selected on the review page (typically the read-only
    baseline).
    ``requested`` = the initiator asked for this scope (agents flag write
    scopes for human attention).
    """

    name: str
    classification: Literal["read", "write", "admin"]
    default: bool
    requested: bool
    description: str


class VendorEntry(BaseModel):
    """Unified vendor read view spanning admin-DB rows and config entries.

    Only carries fields that are meaningful on both sides of the seam:

    * ``key`` — the slug callers pass into the service. For DB rows this is
      the ``api_vendor`` column; for config rows it is the ``entries`` dict
      key.
    * ``flow_kind`` — the discriminator that determines which OAuth flow the
      caller should run. Config entries can offer multiple flows; this field
      surfaces the preferred one (first entry in ``flows``) so callers of the
      unified view do not need to reason about multi-flow config shapes.
    * ``source`` — records where the row came from so surfaces can differentiate
      admin-managed vs platform-shipped registrations.

    Secrets are never surfaced here — ``client_secret`` and other flow-specific
    endpoint detail are dereferenced by the connect-time handlers.
    """

    key: str
    display_name: str
    flow_kind: VendorFlowKind
    client_id: str
    has_client_secret: bool = False
    default_scopes: list[str] | None = None
    source: VendorEntrySource
