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

    Multiple DB registrations for the same vendor slug surface as separate
    entries — each admin-registered OAuth app is its own pick in the vendor
    picker. The ``entry_id`` is stable per-row (registration id when
    source=db; vendor key when source=config).

    * ``entry_id`` — stable UI key for one picker card.
    * ``registration_id`` — the ``oar_...`` id when ``source == "db"``,
      else ``None``.
    * ``key`` — vendor slug (``api_vendor`` for DB rows, ``entries`` dict
      key for config rows). Two DB entries for the same vendor share this.
    * ``name`` — the human label to render as the card's primary text. For
      DB entries this is the admin-picked registration name; for config
      entries this is the vendor's config ``display_name``.
    * ``display_name`` — the vendor's family name (from config, or falling
      back to the admin's registration name when no matching config
      entry). Lets the UI show a subtitle for DB entries whose name differs
      from the vendor family label.
    * ``flow_kind`` — discriminator determining which OAuth flow the caller
      should run.
    * ``source`` — records where the row came from so surfaces can
      differentiate admin-managed vs platform-shipped registrations.

    Secrets are never surfaced here — ``client_secret`` and other
    flow-specific endpoint detail are dereferenced by the connect-time
    handlers.
    """

    entry_id: str
    registration_id: str | None = None
    key: str
    display_name: str
    name: str
    flow_kind: VendorFlowKind
    client_id: str
    has_client_secret: bool = False
    default_scopes: list[str] | None = None
    source: VendorEntrySource
