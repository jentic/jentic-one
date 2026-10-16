"""Token-endpoint origin checks shared by the credential edit and connect writes.

A cross-origin ``token_url`` edit drops the credential's grant under the
refresh lock (``OAuthTokenRepository.acquire_refresh_lock``). A connect
exchanges its code outside any transaction, so its token write takes the same
lock and re-checks the stored endpoint: a grant minted at an origin the
credential no longer points at is discarded rather than stored next to the new
``token_url``, where the next refresh would present it to the new origin.
"""

from __future__ import annotations

from urllib.parse import urlsplit

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


def minted_at_current_origin(minted_at: str | None, current: str | None) -> bool:
    """Whether a grant exchanged at *minted_at* still matches the *current* endpoint.

    *current* is ``OAuthClientCredentialRepository.current_token_url`` read
    inside the token write's transaction, after taking the refresh lock.
    ``None`` for *minted_at* (a flow that reports no endpoint: a provider
    connecting as an account reference, or device flow) has nothing to
    compare and passes.
    """
    return minted_at is None or same_origin(minted_at, current)
