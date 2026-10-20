"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from fastapi import Depends, Request
from jentic.problem_details import Forbidden, Unauthorized

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import compute_effective
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.events.mcp_session import SESSION_ID_HEADER, schedule_mcp_session_emit
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.web.auth import extract_credential


def get_ctx(request: Request) -> Context:
    """Retrieve the application Context from request state."""
    ctx: Context = request.app.state.ctx
    return ctx


TokenVerifier = Callable[[str, Request], Coroutine[Any, Any, Identity]]


def derive_origin(user_agent: str | None) -> Origin:
    """Derive the request origin from the standard User-Agent header."""
    if not user_agent:
        return Origin.API
    ua_lower = user_agent.lower()
    if ua_lower.startswith("jentic-mcp/"):
        return Origin.MCP
    if ua_lower.startswith("jentic-cli/"):
        return Origin.CLI
    if ua_lower.startswith("mozilla/") or ua_lower.startswith("applewebkit/"):
        return Origin.DASHBOARD
    return Origin.API


async def resolve_identity(request: Request) -> Identity:
    """Resolve the caller's identity from the request credential.

    Supports API keys (``x-jentic-api-key`` header) and Bearer tokens. This is
    the single dependency function that all ``get_current_identity`` closures
    delegate through. Tests can override it with
    ``app.dependency_overrides[resolve_identity]`` to inject a fake identity.
    """
    credential = extract_credential(request)

    verify_token: TokenVerifier = request.app.state.verify_token
    try:
        identity = await verify_token(credential, request)
    except (Unauthorized, Forbidden, DatabaseUnavailableError):
        # A database that cannot answer is not a bad credential: the surface's
        # handler turns it into a retryable 503 (the caller is still refused),
        # so a DB blip never reads to a client as "your session is dead".
        raise
    except Exception:
        raise Unauthorized(
            detail="Invalid or expired token",
            instance=request.url.path,
            type="unauthorized",
        ) from None

    identity.origin = derive_origin(request.headers.get("user-agent"))

    # First authenticated MCP request per session emits ``mcp.session_started``
    # (fire-and-forget; two header checks on the non-MCP fast path). This is the
    # control-plane half of the two-plane emit — the broker's identity
    # dependency mirrors it for execute-only sessions.
    schedule_mcp_session_emit(
        getattr(request.app.state, "ctx", None),
        user_agent=request.headers.get("user-agent"),
        session_id=request.headers.get(SESSION_ID_HEADER),
        actor_id=identity.sub,
        actor_type=identity.actor_type.value,
    )
    return identity


class CredentialRecheckResult(StrEnum):
    """The outcome of re-verifying a request's credential mid-response."""

    #: The credential still verifies and its identity still meets the requirements.
    VALID = "valid"
    #: The verifier refused it: expired, revoked, or its actor disabled or deleted.
    REFUSED = "refused"
    #: The credential verifies, but its identity no longer meets the requirements
    #: (a required permission was revoked).
    FORBIDDEN = "forbidden"
    #: The verifier could not answer (the database is unavailable).
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CredentialRecheckOutcome:
    """A recheck's result plus, when ``VALID``, the freshly verified identity."""

    result: CredentialRecheckResult
    identity: Identity | None = None


class CredentialRecheck(Protocol):
    """Re-verify the request's credential against the same requirements as its route."""

    async def __call__(
        self, *, required_permissions: Sequence[str] | None = None
    ) -> CredentialRecheckOutcome: ...


def get_credential_recheck(request: Request) -> CredentialRecheck:
    """A re-verifier for the request's own credential, for responses that outlive the request.

    A streaming response (``/events/stream``) authenticates once, then stays
    open far longer than any single verification. The returned callable runs
    the presented credential back through the app's verifier (#1139) and then
    applies :func:`enforce_identity_requirements` to the identity the verifier
    returns, so a revoked permission is seen as well as a dead credential
    (#1836). The identity carries whatever permissions the verifier resolves:
    live grants for API keys and user access tokens, the embedded set for a
    login JWT until it is re-minted.

    A database that cannot answer reads as ``UNAVAILABLE``, a requirement the
    identity no longer meets as ``FORBIDDEN``, and any other failure as
    ``REFUSED``. The caller treats all three as not valid (fail closed); a
    reconnect gets the precise 401/403/503.
    """

    async def _recheck(
        *, required_permissions: Sequence[str] | None = None
    ) -> CredentialRecheckOutcome:
        verify_token: TokenVerifier = request.app.state.verify_token
        try:
            identity = await verify_token(extract_credential(request), request)
        except DatabaseUnavailableError:
            return CredentialRecheckOutcome(CredentialRecheckResult.UNAVAILABLE)
        except Exception:
            return CredentialRecheckOutcome(CredentialRecheckResult.REFUSED)
        try:
            enforce_identity_requirements(
                identity,
                instance=request.url.path,
                required_permissions=required_permissions,
            )
        except Forbidden:
            return CredentialRecheckOutcome(CredentialRecheckResult.FORBIDDEN)
        return CredentialRecheckOutcome(CredentialRecheckResult.VALID, identity)

    return _recheck


def enforce_identity_requirements(
    identity: Identity,
    *,
    instance: str,
    required_permissions: Sequence[str] | None = None,
    allow_expired_password: bool = False,
    require_actor_type: ActorType | None = None,
) -> None:
    """Raise ``Forbidden`` unless ``identity`` meets a route's requirements.

    The one place the checks behind :func:`get_current_identity` live, so a
    response that re-verifies mid-flight (:func:`get_credential_recheck`)
    applies exactly what admitted the request.
    """
    if not allow_expired_password and identity.must_change_password:
        raise Forbidden(
            detail="Password rotation required before accessing this resource",
            instance=instance,
            type="password_rotation_required",
        )

    if require_actor_type and identity.actor_type != require_actor_type:
        raise Forbidden(
            detail=f"This endpoint is restricted to {require_actor_type}s",
            instance=instance,
            type="forbidden",
        )

    if required_permissions:
        # Expand the caller's grants through the static implication map so the
        # advertised semantics hold uniformly at enforcement (e.g. `*:write`
        # implies `*:read`). Without this, callers whose permissions arrive
        # unexpanded — API keys resolved straight from `actor_permission_grants`
        # (see shared/auth/api_key_resolver.py) — would be 403'd on a read
        # route they hold the write permission for, while the same actor admitted
        # via an access token (expanded by PermissionService) would pass.
        caller_perms = compute_effective(set(identity.permissions))
        if "org:admin" not in caller_perms and not caller_perms.intersection(required_permissions):
            raise Forbidden(
                detail=f"This action requires one of: {', '.join(required_permissions)}",
                instance=instance,
                type="forbidden",
            )


def get_current_identity(
    *,
    required_permissions: list[str] | None = None,
    allow_expired_password: bool = False,
    require_actor_type: ActorType | None = None,
) -> Any:
    """Factory returning a FastAPI dependency that resolves and checks the caller's identity.

    Parameters
    ----------
    required_permissions:
        If set, the caller must hold at least one of these permissions (or org:admin).
    allow_expired_password:
        If True, skip the must_change_password check.
    require_actor_type:
        If set, the caller's actor_type must match (e.g. "agent").
    """

    async def _dependency(
        request: Request,
        identity: Identity = Depends(resolve_identity),
    ) -> Identity:
        # The endpoint reference reads ``required_permissions`` and
        # ``require_actor_type`` off this closure, so both stay free variables here.
        enforce_identity_requirements(
            identity,
            instance=request.url.path,
            required_permissions=required_permissions,
            allow_expired_password=allow_expired_password,
            require_actor_type=require_actor_type,
        )
        return identity

    return Depends(_dependency)
