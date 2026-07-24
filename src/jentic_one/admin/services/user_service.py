"""User management service."""

from __future__ import annotations

from jentic_one.admin.core.permissions import IMPLICATION_MAP, compute_effective
from jentic_one.admin.repos import (
    AuditRepository,
    InviteTokenRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import (
    ConflictError,
    EmailAlreadyExistsError,
    UserNotFoundError,
)
from jentic_one.admin.services.invite_service import InviteService
from jentic_one.admin.services.metrics import audit_events_counter
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.schemas.invites import InviteIssued
from jentic_one.admin.services.schemas.users import (
    EffectivePermissionView,
    UserCreatedView,
    UserCreatePayload,
    UserUpdatePayload,
    UserView,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from jentic_one.shared.models.audit import AuditAction, AuditTargetType


def _derive_invite_state(stored: str, user_id: str, active_invite_user_ids: set[str]) -> str:
    """Overlay the read-time EXPIRED invite state (never persisted).

    A ``pending`` user with no unredeemed, unexpired invite token has a lapsed
    invite — surface it as ``expired`` so admins know to regenerate. Only
    ``pending`` is overlaid; every other stored state (``redeemed`` and
    ``accepted`` — the latter is persisted for external-auth users) passes
    through unchanged. Mirrors the AccessRequest EXPIRED derivation: the DB keeps
    ``pending`` and the token's ``expires_at`` is the source of truth.
    """
    if stored == InviteState.PENDING and user_id not in active_invite_user_ids:
        return InviteState.EXPIRED
    return stored


def _build_effective_views(assigned: list[str]) -> list[EffectivePermissionView]:
    """Build effective permission views from assigned permissions."""
    effective_set = compute_effective(set(assigned))
    views: list[EffectivePermissionView] = []
    assigned_set = set(assigned)
    for perm_name in sorted(effective_set):
        if perm_name in assigned_set:
            views.append(EffectivePermissionView(name=perm_name, implied_by=None))
        else:
            implied_by = None
            for source in assigned_set:
                transitive = _expand_one(source)
                if perm_name in transitive:
                    implied_by = source
                    break
            views.append(EffectivePermissionView(name=perm_name, implied_by=implied_by))
    return views


def _expand_one(permission_name: str) -> set[str]:
    """Get the full transitive closure of a single permission."""
    result: set[str] = set()
    frontier = [permission_name]
    while frontier:
        current = frontier.pop()
        implied = IMPLICATION_MAP.get(current, set())
        for p in implied:
            if p not in result:
                result.add(p)
                frontier.append(p)
    return result


class UserService:
    """Manages user lifecycle: create, read, update, disable, delete."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        cursor: str | None = None,
        limit: int = 50,
        invite_state: InviteState | None = None,
    ) -> Page[UserView]:
        # NOTE: the `invite_state` filter matches the STORED column, so it can't
        # filter by the derived EXPIRED state (never persisted) — `EXPIRED`
        # returns nothing and `PENDING` may return rows that render as expired.
        # This mirrors AccessRequestService.list_all, which filters on stored
        # status and derives EXPIRED only in the view. If filtering by expired is
        # ever needed, translate it to `PENDING AND user_id NOT IN (active)` here.
        cursor_dt = None
        if cursor is not None:
            cursor_dt, _ = decode_cursor(cursor)

        async with self._ctx.admin_db.session() as session:
            users = await UserRepository.list_all(
                session, limit=limit + 1, cursor=cursor_dt, invite_state=invite_state
            )

        has_more = len(users) > limit
        if has_more:
            users = users[:limit]

        user_ids = [u.id for u in users]
        perm_service = PermissionService(self._ctx)
        perms_map = await perm_service.project_for_users(user_ids)

        # Derive the EXPIRED invite state at read time (never persisted, mirroring
        # AccessRequest's EXPIRED overlay): a still-pending user with no unredeemed,
        # unexpired token has a lapsed invite. One batch query — no N+1.
        pending_ids = [u.id for u in users if u.invite_state == InviteState.PENDING]
        active_invite_ids: set[str] = set()
        if pending_ids:
            async with self._ctx.admin_db.session() as session:
                active_invite_ids = await InviteTokenRepository.user_ids_with_active_invite(
                    session, pending_ids
                )

        views = [
            UserView(
                id=u.id,
                email=u.email,
                first_name=u.first_name,
                last_name=u.last_name,
                name=f"{u.first_name} {u.last_name}",
                active=u.active,
                auth_provider=u.auth_provider,
                invite_state=_derive_invite_state(u.invite_state, u.id, active_invite_ids),
                must_change_password=u.must_change_password,
                external_subject_id=u.external_subject_id,
                assigned=perms_map.get(u.id, []),
                effective=_build_effective_views(perms_map.get(u.id, [])),
                created_at=u.created_at,
                updated_at=u.updated_at,
            )
            for u in users
        ]

        next_cursor = None
        if has_more and users:
            next_cursor = encode_cursor(users[-1].created_at, users[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def create(self, payload: UserCreatePayload, *, identity: Identity) -> UserCreatedView:
        granted_by = identity.sub
        perm_service = PermissionService(self._ctx)
        if payload.permissions:
            await perm_service.validate_grants(granted_by, payload.permissions)

        async with self._ctx.admin_db.transaction() as session:
            existing = await UserRepository.get_by_email(session, payload.email)
            if existing is not None:
                raise EmailAlreadyExistsError(payload.email)

            user = await UserRepository.create(
                session,
                email=payload.email,
                first_name=payload.first_name,
                last_name=payload.last_name,
                created_by=granted_by,
            )

            await UserSecretRepository.create(session, user_id=user.id, created_by=granted_by)

            if payload.permissions:
                await UserPermissionGrantRepository.set_permissions(
                    session,
                    user.id,
                    permissions=set(payload.permissions),
                    granted_by=granted_by,
                    created_by=granted_by,
                )

            invite_service = InviteService(self._ctx)
            invite = await invite_service.issue_for_user(session, user.id, actor_id=granted_by)

            await AuditRepository.record(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.USER,
                target_id=user.id,
                actor_type=identity.actor_type,
                actor_id=granted_by,
                after={
                    "email": payload.email,
                    "first_name": payload.first_name,
                    "last_name": payload.last_name,
                },
            )
            audit_events_counter.add(
                1, {"action": AuditAction.CREATE, "target_type": AuditTargetType.USER}
            )

        user_view = await self.get_by_id(user.id)
        return UserCreatedView(
            user=user_view, invite_token=invite.token, invite_expires_at=invite.expires_at
        )

    async def get_by_id(self, user_id: str) -> UserView:
        async with self._ctx.admin_db.session() as session:
            user = await UserRepository.get_by_id(session, user_id)
            if user is None:
                raise UserNotFoundError(user_id)
            # Derive EXPIRED at read time (see list_all): a pending user with no
            # active invite token has a lapsed invite.
            active_invite_ids: set[str] = set()
            if user.invite_state == InviteState.PENDING:
                active_invite_ids = await InviteTokenRepository.user_ids_with_active_invite(
                    session, [user.id]
                )

        perm_service = PermissionService(self._ctx)
        assigned = await perm_service.get_assigned_for_user(user_id)
        return UserView(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            name=f"{user.first_name} {user.last_name}",
            active=user.active,
            auth_provider=user.auth_provider,
            invite_state=_derive_invite_state(user.invite_state, user.id, active_invite_ids),
            must_change_password=user.must_change_password,
            external_subject_id=user.external_subject_id,
            assigned=assigned,
            effective=_build_effective_views(assigned),
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    async def get_self(self, user_id: str) -> UserView:
        return await self.get_by_id(user_id)

    async def update(
        self, user_id: str, payload: UserUpdatePayload, *, identity: Identity
    ) -> UserView:
        async with self._ctx.admin_db.transaction() as session:
            user = await UserRepository.get_by_id(session, user_id)
            if user is None:
                raise UserNotFoundError(user_id)

            before = {
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
            }

            if payload.email is not None:
                existing = await UserRepository.get_by_email(session, payload.email)
                if existing is not None and existing.id != user_id:
                    raise EmailAlreadyExistsError(payload.email)

            await UserRepository.update(
                session,
                user_id,
                email=payload.email,
                first_name=payload.first_name,
                last_name=payload.last_name,
            )

            after = {
                "email": payload.email if payload.email is not None else user.email,
                "first_name": (
                    payload.first_name if payload.first_name is not None else user.first_name
                ),
                "last_name": (
                    payload.last_name if payload.last_name is not None else user.last_name
                ),
            }

            await AuditRepository.record(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.USER,
                target_id=user_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before=before,
                after=after,
            )
            audit_events_counter.add(
                1, {"action": AuditAction.UPDATE, "target_type": AuditTargetType.USER}
            )

        return await self.get_by_id(user_id)

    async def delete(self, user_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            user = await UserRepository.get_by_id(session, user_id)
            if user is None:
                raise UserNotFoundError(user_id)
            await UserRepository.update(
                session,
                user_id,
                email=f"deleted-{user_id}@local",
                active=False,
            )
            await AuditRepository.record(
                session,
                action=AuditAction.DELETE,
                target_type=AuditTargetType.USER,
                target_id=user_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
            )
            audit_events_counter.add(
                1, {"action": AuditAction.DELETE, "target_type": AuditTargetType.USER}
            )

    async def disable(self, user_id: str, *, identity: Identity) -> bool:
        async with self._ctx.admin_db.transaction() as session:
            await UserRepository.disable(session, user_id)
            await AuditRepository.record(
                session,
                action=AuditAction.DISABLE,
                target_type=AuditTargetType.USER,
                target_id=user_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
            )
            audit_events_counter.add(
                1, {"action": AuditAction.DISABLE, "target_type": AuditTargetType.USER}
            )
        return True

    async def enable(self, user_id: str, *, identity: Identity) -> bool:
        async with self._ctx.admin_db.transaction() as session:
            await UserRepository.enable(session, user_id)
            await AuditRepository.record(
                session,
                action=AuditAction.ENABLE,
                target_type=AuditTargetType.USER,
                target_id=user_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
            )
            audit_events_counter.add(
                1, {"action": AuditAction.ENABLE, "target_type": AuditTargetType.USER}
            )
        return True

    async def reissue_invite(self, user_id: str, *, identity: Identity) -> InviteIssued:
        async with self._ctx.admin_db.session() as session:
            user = await UserRepository.get_by_id(session, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        if user.invite_state == InviteState.REDEEMED:
            raise ConflictError("Cannot reissue invite for a user who has already redeemed")

        invite_service = InviteService(self._ctx)
        issued = await invite_service.reissue(user_id, identity=identity)
        return issued
