"""Source-agnostic OAuth app configuration for one connect session.

The connect-session state machine has two sources for the OAuth app material
that a handler needs at ``prepare`` + ``begin`` time:

* **Config source** — the platform-shipped ``VendorAuthConfig.flows[]`` entry
  (legacy embedded path). Materialised into ``oauth_client_credentials`` /
  ``device_authorization_credentials`` at ``prepare`` time.
* **DB source** — an admin-registered ``oauth_app_registrations`` row (auth-code
  or device-flow extension). Materialised as a FK on the credential row
  (``credentials.oauth_app_registration_id``) so refreshes dereference the shared
  registration rather than duplicating client material per grant.

``SessionApp`` is the flow-agnostic normalisation the service hands the
handler — the handler branches on ``registration_id`` (None = write the
legacy aux row; non-None = set the FK and skip the aux row's app-config
columns for auth-code, or populate them by-value for device flow where the
aux row also carries transient state).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionApp:
    """Normalised OAuth-app config for one connect session.

    ``client_secret_provider`` is a lazy callable so device flow (a public
    client, no secret) can pass a no-op / None-returning provider without
    eagerly decrypting a secret it will never send. Auth-code invokes it
    exactly once at ``complete_from_callback`` time.

    ``registration_id`` is the discriminator the handler branches on:
    * ``None`` — legacy embedded path (write to aux table with all fields).
    * non-None — DB-registration path (set FK on credential, skip
      aux-table app-config writes for auth-code; device flow still writes
      its transient-state aux row).
    """

    flow_kind: str
    client_id: str
    client_secret_provider: Callable[[], str] | None
    default_scopes: list[str]
    registration_id: str | None
    # Auth-code fields (populated for authorization_code).
    authorize_url: str | None = None
    token_url: str | None = None
    # Device-flow fields (populated for device_authorization).
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
