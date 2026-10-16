"""Token-endpoint origin checks shared by the credential edit and connect writes.

A cross-origin ``token_url`` edit drops the credential's grant under the
refresh lock (``OAuthTokenRepository.acquire_refresh_lock``). A connect
exchanges its code outside any transaction, so its token write takes the same
lock and re-checks the stored endpoint: a grant minted at an origin the
credential no longer points at is discarded rather than stored next to the new
``token_url``, where the next refresh would present it to the new origin.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import OAuthAppRegistrationRepository, OAuthClientCredentialRepository

_DEFAULT_PORTS = {"http": 80, "https": 443}


def url_origin(url: str) -> tuple[str, str, int | None]:
    """``(scheme, host, port)`` of *url*, with the scheme's default port filled in."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, (parts.hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme)


def same_origin(new_url: str, stored_url: str | None) -> bool:
    """Whether *new_url* keeps the origin of *stored_url* (no stored URL never matches)."""
    return bool(stored_url) and url_origin(new_url) == url_origin(stored_url or "")


async def current_token_url(session: Any, credential: Credential) -> str | None:
    """The token endpoint an authorization-code *credential* resolves to now.

    The shared app registration's when the credential references one, else
    the embedded client config's; ``None`` when there is none. Device-flow
    credentials are not covered: their token_url cannot move to another
    origin, and is frozen while a flow is pending.
    """
    if credential.oauth_app_registration_id is not None:
        registration = await OAuthAppRegistrationRepository.get_by_id(
            session, credential.oauth_app_registration_id
        )
        details = registration.authorization_code_details if registration else None
        return details.token_url if details else None
    occ = await OAuthClientCredentialRepository.get_by_credential(session, credential.id)
    return occ.token_url if occ else None


async def minted_at_current_origin(
    session: Any, credential: Credential, minted_at: str | None
) -> bool:
    """Whether a grant exchanged at *minted_at* still matches *credential*'s endpoint.

    Call it inside the token write's transaction, after taking the refresh
    lock. ``None`` (a flow that reports no endpoint: a provider connecting as
    an account reference, or device flow) has nothing to compare and passes.
    """
    if minted_at is None:
        return True
    return same_origin(minted_at, await current_token_url(session, credential))
