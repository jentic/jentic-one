"""Pure token verification producing an Identity."""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.tokens import InvalidTokenError, decode_jwt
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)


def scopes_to_permissions(scopes: Sequence[object]) -> list[str]:
    """Translate OAuth2 scopes carried on a token into internal permissions.

    **The** place where this deployment's OAuth2 wire vocabulary crosses into its
    internal authorization vocabulary. A token minted by this deployment's
    authorization server carries its grant as scopes — in the ``scopes`` claim of a
    JWT, or in the ``access_tokens.scopes`` snapshot behind an opaque token;
    everything downstream of verification — route guards,
    :class:`~jentic_one.shared.auth.identity.Identity`, query scoping — reasons in
    permissions only. Callers: :func:`verify_token` below and
    :class:`jentic_one.broker.repos.token_resolver.InProcessTokenResolver`.

    Two other places depend on the same identity without converting through here:
    :data:`jentic_one.shared.auth.permission_catalog.MCP_TOOL_SCOPES` (an OAuth2
    ceiling defined as a permission baseline) and
    :func:`jentic_one.auth.web.routers.authorize._scope_to_permission_description`
    (consent copy looked up in the permission catalogue). Changing the translation
    here means revisiting both.

    The translation is identity today: the two formats use the same colon-form
    strings, so a scope *is* a permission name. Keeping it a named function means
    the day they diverge (a scope prefix, an audience qualifier, a coarser wire
    grant) there is exactly one function to change rather than an inlined
    ``list(...)`` to find.
    """
    return [str(s) for s in scopes]


async def resolve_permissions_for_actor(
    ctx: Context,
    actor_type: ActorType,
    actor_id: str,
    parent_actor_id: str | None,
) -> tuple[list[str], list[str]]:
    """Resolve native and inherited permissions for an actor by type.

    Returns:
        tuple[list[str], list[str]]: (permissions, parent_permissions)
    """
    # NOTE: Moving this to module-level would cause a circular dependency (shared -> admin).
    # A test-arch exception will handle this lazy import until permission
    # resolution is extracted out of the admin surface.
    from jentic_one.admin.services.permission_service import PermissionService

    svc = PermissionService(ctx)
    permissions: list[str] = []
    parent_permissions: list[str] = []

    if actor_type == ActorType.USER:
        view = await svc.get_effective_for_user(actor_id)
        permissions = view.effective
    elif actor_type == ActorType.AGENT:
        # TODO: fetch the agent's direct permissions via svc.get_effective_for_agent
        if parent_actor_id:
            view = await svc.get_effective_for_user(parent_actor_id)
            parent_permissions = view.effective
        else:
            logger.warning("Agent token missing parent_actor_id")
    # ActorType.SERVICE_ACCOUNT (retired, theme-8 Phase 2) deliberately resolves
    # no DB permissions: the old dispatch hit a stub that always returned an
    # empty view, and nothing issues SA tokens any more.

    return permissions, parent_permissions


async def verify_token(token: str, *, secret: str, ctx: Context) -> Identity:
    """Decode and verify a JWT, returning the Identity.

    Fail-closed on the actor type (#862): every JWT we mint carries an
    explicit ``actor_type`` claim, so a validly-signed token without one —
    or with a value we don't recognise — is refused rather than assumed to
    be a USER (the most-privileged interpretation). Raises the same
    ``InvalidTokenError`` as a bad signature; the web layer maps it to 401.

    When the JWT already embeds ``permissions`` in its claims (e.g. login
    JWTs), those are trusted directly — no DB lookup. Otherwise falls back
    to the database-resolved permission path for backwards compatibility.
    """
    claims = decode_jwt(token, secret)
    actor_type_claim = claims.get("actor_type")
    if actor_type_claim is None:
        # The wire response is deliberately uniform with any other bad token
        # (401, no oracle for forgers) — log the real cause so an operator
        # can tell a claim-less token from a bad signature or wrong secret.
        logger.warning("jwt_actor_type_missing", sub=claims.get("sub"))
        raise InvalidTokenError("Token does not declare an actor_type")
    try:
        actor_type = ActorType(actor_type_claim)
    except ValueError as exc:
        # Typed, deliberate rejection instead of relying on the web layer's
        # catch-all to absorb a ValueError from the enum constructor.
        logger.warning(
            "jwt_actor_type_unknown", sub=claims.get("sub"), actor_type=str(actor_type_claim)
        )
        raise InvalidTokenError("Token declares an unknown actor_type") from exc
    sub = claims["sub"]
    parent_actor_id = claims.get("parent_actor_id")

    embedded_permissions = claims.get("permissions")
    if embedded_permissions is not None and isinstance(embedded_permissions, list):
        permissions = [str(p) for p in embedded_permissions]
        parent_permissions: list[str] = []
    else:
        permissions, parent_permissions = await resolve_permissions_for_actor(
            ctx, actor_type, sub, parent_actor_id
        )

    # A token minted by this deployment's own authorization server carries its
    # grant as OAuth2 scopes; fold them into the permission set the rest of the
    # platform enforces on. Order-preserving dedup so a scope that duplicates a
    # resolved permission does not appear twice on the Identity.
    scopes_raw = claims.get("scopes")
    if isinstance(scopes_raw, list):
        permissions = list(dict.fromkeys(permissions + scopes_to_permissions(scopes_raw)))

    return Identity(
        sub=sub,
        email=claims.get("email", ""),
        permissions=permissions,
        parent_permissions=parent_permissions,
        must_change_password=claims.get("must_change_password", False),
        actor_type=actor_type,
        parent_actor_id=parent_actor_id,
    )
