"""Auth injection logic — decrypts credentials and produces request headers/query params."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialLocation, CredentialType


@dataclass(frozen=True, slots=True)
class InjectionResult:
    """Headers, query parameters, and cookies produced by auth injection."""

    headers: dict[str, str]
    query_params: dict[str, str]
    cookies: dict[str, str]


def inject_auth(
    resolved: ResolvedCredential, *, ctx: Context, access_token: str | None = None
) -> InjectionResult:
    """Produce auth headers/query from a resolved credential.

    For OAuth2: the caller must supply a pre-validated access_token (from TokenRefresher).
    """
    if resolved.wire_type == CredentialType.BEARER_TOKEN:
        return _inject_bearer(resolved, ctx)
    if resolved.wire_type == CredentialType.API_KEY:
        return _inject_api_key(resolved, ctx)
    if resolved.wire_type == CredentialType.BASIC:
        return _inject_basic(resolved, ctx)
    if resolved.wire_type == CredentialType.OAUTH2:
        return _inject_oauth2(access_token)
    if resolved.wire_type == CredentialType.NO_AUTH:
        # A no-auth credential injects nothing — the API needs no secret (#603).
        return InjectionResult(headers={}, query_params={}, cookies={})
    # An unknown wire type must fail loudly rather than silently inject nothing:
    # a quiet empty injection would send an unauthenticated request and surface
    # as a confusing upstream 401/403 far from the cause. Every known type is
    # handled above (exhaustive over CredentialType).
    raise ValueError(f"Unsupported credential wire_type for injection: {resolved.wire_type!r}")


def _inject_bearer(resolved: ResolvedCredential, ctx: Context) -> InjectionResult:
    if resolved.encrypted_secret is None:
        raise ValueError(f"Bearer credential {resolved.credential_id} missing encrypted_secret")
    token = ctx.encryption.decrypt(resolved.encrypted_secret)
    return InjectionResult(
        headers={"Authorization": f"Bearer {token}"}, query_params={}, cookies={}
    )


def _inject_api_key(resolved: ResolvedCredential, ctx: Context) -> InjectionResult:
    if resolved.encrypted_secret is None:
        raise ValueError(f"API key credential {resolved.credential_id} missing encrypted_secret")
    key = ctx.encryption.decrypt(resolved.encrypted_secret)
    location = resolved.location or CredentialLocation.HEADER
    field_name = resolved.field_name or "Authorization"

    if location == CredentialLocation.QUERY:
        return InjectionResult(headers={}, query_params={field_name: key}, cookies={})
    if location == CredentialLocation.COOKIE:
        return InjectionResult(headers={}, query_params={}, cookies={field_name: key})
    return InjectionResult(headers={field_name: key}, query_params={}, cookies={})


def _inject_basic(resolved: ResolvedCredential, ctx: Context) -> InjectionResult:
    if resolved.username is None:
        raise ValueError(f"Basic credential {resolved.credential_id} missing username")
    if resolved.encrypted_password is None:
        raise ValueError(f"Basic credential {resolved.credential_id} missing encrypted_password")
    password = ctx.encryption.decrypt(resolved.encrypted_password)
    encoded = base64.b64encode(f"{resolved.username}:{password}".encode()).decode()
    return InjectionResult(
        headers={"Authorization": f"Basic {encoded}"}, query_params={}, cookies={}
    )


def _inject_oauth2(access_token: str | None) -> InjectionResult:
    if access_token is None:
        raise ValueError("OAuth2 injection requires a pre-validated access_token")
    return InjectionResult(
        headers={"Authorization": f"Bearer {access_token}"}, query_params={}, cookies={}
    )
