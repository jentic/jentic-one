"""Human-readable descriptions of OAuth scopes for the consent pages.

Presentation copy, not business logic: it holds no ``Context``, reads no
repository, and its only consumer is :mod:`jentic_one.auth.web.pages`. It
lives beside :mod:`jentic_one.auth.web.theme` for that reason — the wording a
consent page shows is not a domain contract.
"""

from __future__ import annotations

from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    AGENTS_WRITE,
    ALL_PERMISSIONS,
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
)

_HIDDEN_SCOPES: frozenset[str] = frozenset({"openid"})

_OIDC_SCOPE_DESCRIPTIONS: dict[str, str] = {
    "email": "View your email address",
    "profile": "View your basic profile information",
}

_PLATFORM_SCOPE_DESCRIPTIONS: dict[str, str] = {
    AGENTS_READ: "View agents",
    AGENTS_WRITE: "Create and manage agents",
    CREDENTIALS_READ: "View credential metadata",
    CREDENTIALS_WRITE: "Create and manage credentials",
}


def scope_to_permission_description(scope: str) -> str | None:
    """Map OAuth scopes to human-readable permission descriptions.

    Returns None for scopes that should not be displayed (e.g. openid).
    Falls back to the permission catalog description for platform scopes,
    or a generic label for completely unknown scopes.

    A vocabulary crossing point: the argument is a scope off the authorization
    request, the result describes the permission the user is about to grant. The
    lookup into :data:`ALL_PERMISSIONS` works because the two formats are the same
    colon-form strings — see
    :func:`jentic_one.shared.auth.verify.scopes_to_permissions`, the other place
    that depends on that identity.
    """
    if scope in _HIDDEN_SCOPES:
        return None
    if scope in _OIDC_SCOPE_DESCRIPTIONS:
        return _OIDC_SCOPE_DESCRIPTIONS[scope]
    if scope in _PLATFORM_SCOPE_DESCRIPTIONS:
        return _PLATFORM_SCOPE_DESCRIPTIONS[scope]
    perm = ALL_PERMISSIONS.get(scope)
    if perm is not None:
        return perm.description
    return f"Access: {scope}"
