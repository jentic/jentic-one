"""Repository for AccessRequest CRUD operations."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.errors import DuplicatePendingItemError
from jentic_one.control.core.schema.access_request_items import (
    RULE_BEARING_COMBINATIONS,
    AccessRequestItem,
)
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.shared.models.access_requests import AccessRequestItemStatus, AccessRequestStatus
from jentic_one.shared.models.api_identity import slugify_api_field


def compute_aggregate_status(item_statuses: list[str]) -> AccessRequestStatus:
    """Derive the aggregate request status from its items' statuses."""
    if AccessRequestItemStatus.PENDING in item_statuses:
        return AccessRequestStatus.PENDING
    if all(s == AccessRequestItemStatus.APPROVED for s in item_statuses):
        return AccessRequestStatus.APPROVED
    if not any(s == AccessRequestItemStatus.APPROVED for s in item_statuses):
        return AccessRequestStatus.DENIED
    return AccessRequestStatus.PARTIALLY_APPROVED


def _normalize_reference(reference: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """Canonicalize a toolkit resource_reference for duplicate comparison.

    Only the vendor/name/version triple is meaningful for resolution, so two
    references that differ only in inert extra keys are considered the same,
    and a missing reference normalizes to ``None`` (so two no-reference items
    still compare equal). Vendor/name are slugified to match the registry's
    normalized identity (issue #656) so a plan filed with a raw domain
    (``httpbin.org``) dedups against one filed with the slug (``httpbin-org``);
    version is lowercased/stripped to match the case-insensitive handling at
    resolve time.
    """
    if not reference:
        return None
    vendor = slugify_api_field(str(reference.get("vendor", "")))
    name = slugify_api_field(str(reference.get("name", "")))
    version = str(reference.get("version", "")).strip().lower()
    return (vendor, name, version)


class AccessRequestRepository:
    """Data access layer for AccessRequest entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        actor_id: str,
        reason: str | None,
        requested_by: str,
        approve_url: str,
        expires_at: dt.datetime,
        items: list[dict[str, Any]],
        created_by: str,
        filer_owner_id: str | None = None,
    ) -> AccessRequest:
        request = AccessRequest(
            actor_id=actor_id,
            reason=reason,
            requested_by=requested_by,
            approve_url=approve_url,
            expires_at=expires_at,
            status=AccessRequestStatus.PENDING,
            created_by=created_by,
            filer_owner_id=filer_owner_id,
        )
        for item_data in items:
            rules = item_data.get("rules")
            # Only substitute the read-only default for item types whose rules are
            # actually enforced (credential:bind). Stamping default rules onto a
            # non-rule-bearing item (e.g. toolkit:bind) would persist a
            # non-enforceable allowlist that the approval guard then has to reject.
            if not rules and (item_data["resource_type"], item_data["action"]) in (
                RULE_BEARING_COMBINATIONS
            ):
                rules = [{"effect": "allow", "methods": ["GET"]}]
            request.items.append(
                AccessRequestItem(
                    actor_id=actor_id,
                    resource_type=item_data["resource_type"],
                    action=item_data["action"],
                    resource_id=item_data.get("resource_id"),
                    resource_reference=item_data.get("resource_reference"),
                    to_type=item_data.get("to_type"),
                    to_id=item_data.get("to_id"),
                    rules=rules,
                    status=AccessRequestItemStatus.PENDING,
                    created_by=created_by,
                )
            )
        session.add(request)
        try:
            await session.flush()
        except IntegrityError as exc:
            if "uq_access_request_items_pending_dedup" in str(exc):
                raise DuplicatePendingItemError() from exc
            raise
        return request

    @staticmethod
    async def get(
        session: AsyncSession,
        request_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> AccessRequest | None:
        stmt = (
            select(AccessRequest)
            .where(AccessRequest.id == request_id)
            .options(selectinload(AccessRequest.items))
        )
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        actor_id: str | None = None,
        status: str | None = None,
        cursor: tuple[dt.datetime, str] | None = None,
        limit: int = 50,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[AccessRequest]:
        stmt = (
            select(AccessRequest)
            .options(selectinload(AccessRequest.items))
            .order_by(AccessRequest.filed_at.desc(), AccessRequest.id.desc())
        )
        if actor_id is not None:
            stmt = stmt.where(AccessRequest.actor_id == actor_id)
        if status is not None:
            stmt = stmt.where(AccessRequest.status == status)
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (AccessRequest.filed_at < cursor_ts)
                | ((AccessRequest.filed_at == cursor_ts) & (AccessRequest.id < cursor_id))
            )
        for f in filters or ():
            stmt = stmt.where(f)
        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def withdraw(
        session: AsyncSession,
        request_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> AccessRequest | None:
        stmt = (
            select(AccessRequest)
            .where(AccessRequest.id == request_id)
            .options(selectinload(AccessRequest.items))
        )
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        request = result.scalar_one_or_none()
        if request is None:
            return None
        request.status = AccessRequestStatus.WITHDRAWN
        for item in request.items:
            if item.status == AccessRequestItemStatus.PENDING:
                item.status = AccessRequestItemStatus.WITHDRAWN
        await session.flush()
        return request

    @staticmethod
    async def find_pending_duplicate(
        session: AsyncSession,
        *,
        actor_id: str,
        resource_type: str,
        action: str,
        to_id: str | None,
        resource_id: str | None,
        resource_reference: dict[str, Any] | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> AccessRequestItem | None:
        stmt = (
            select(AccessRequestItem)
            .join(AccessRequest)
            .options(selectinload(AccessRequestItem.access_request))
            .where(
                AccessRequestItem.status == AccessRequestItemStatus.PENDING,
                AccessRequest.status == AccessRequestStatus.PENDING,
                AccessRequestItem.actor_id == actor_id,
                AccessRequestItem.resource_type == resource_type,
                AccessRequestItem.action == action,
            )
        )
        if to_id is None:
            stmt = stmt.where(AccessRequestItem.to_id.is_(None))
        else:
            stmt = stmt.where(AccessRequestItem.to_id == to_id)
        if resource_id is None:
            stmt = stmt.where(AccessRequestItem.resource_id.is_(None))
        else:
            stmt = stmt.where(AccessRequestItem.resource_id == resource_id)
        for f in filters or ():
            stmt = stmt.where(f)

        # A reference-only request (e.g. toolkit:bind by vendor/name) carries
        # NULL to_id/resource_id, so the columns above can't distinguish
        # stripe.com/api from github.com/api. Comparing the JSON
        # resource_reference in SQL is dialect-specific, so when there is no
        # resource_id to key on we match the remaining candidates in Python on
        # a normalized reference key (None == None, so two no-reference items
        # still compare equal).
        if resource_id is None:
            want = _normalize_reference(resource_reference)
            result = await session.execute(stmt)
            for item in result.scalars():
                if _normalize_reference(item.resource_reference) == want:
                    return item
            return None

        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def amend_item(
        session: AsyncSession,
        item_id: str,
        *,
        rules: list[dict[str, Any]] | None = None,
        resource_id: str | None = None,
        to_id: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> AccessRequestItem | None:
        stmt = select(AccessRequestItem).where(AccessRequestItem.id == item_id)
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None or item.status != AccessRequestItemStatus.PENDING:
            return None
        if rules is not None:
            item.rules = rules
        if resource_id is not None:
            item.resource_id = resource_id
        if to_id is not None:
            # The provisioning-plan wizard writes the freshly-created toolkit id
            # onto a credential:bind item's bind target after Step 1. to_type is
            # always "toolkit" for a credential:bind, so set it in lock-step.
            item.to_id = to_id
            item.to_type = "toolkit"
        await session.flush()
        return item

    @staticmethod
    async def set_applied_effects(
        session: AsyncSession,
        item_id: str,
        effects: dict[str, Any],
    ) -> None:
        """Persist the applied effects on a decided item.

        Always writes a non-null JSON blob (applied, skipped, or already-applied),
        so a populated ``applied_effects`` is the durable ack that the effect was
        driven. An approved admin-effect item with ``applied_effects IS NULL`` is
        therefore an un-acked effect awaiting re-drive.
        """
        stmt = select(AccessRequestItem).where(AccessRequestItem.id == item_id)
        result = await session.execute(stmt)
        item = result.scalar_one()
        item.applied_effects = effects
        await session.flush()

    @staticmethod
    async def list_unacked_admin_effect_items(
        session: AsyncSession,
        request_id: str,
        *,
        admin_effect_keys: Sequence[tuple[str, str]],
    ) -> list[AccessRequestItem]:
        """Return the request's approved admin-effect items that are still un-acked.

        An item is un-acked when it is APPROVED, its ``(resource_type, action)`` is
        one of the admin-effect combinations, and ``applied_effects IS NULL``. This
        is the durable set of admin effects that a reconcile pass must (re-)drive.
        """
        if not admin_effect_keys:
            return []
        stmt = (
            select(AccessRequestItem)
            .where(
                AccessRequestItem.access_request_id == request_id,
                AccessRequestItem.status == AccessRequestItemStatus.APPROVED,
                AccessRequestItem.applied_effects.is_(None),
                tuple_(AccessRequestItem.resource_type, AccessRequestItem.action).in_(
                    list(admin_effect_keys)
                ),
            )
            .options(selectinload(AccessRequestItem.access_request))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def decide_item(
        session: AsyncSession,
        item_id: str,
        decision: str,
        *,
        decided_by: str,
        decision_reason: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> AccessRequestItem | None:
        stmt = (
            select(AccessRequestItem)
            .where(AccessRequestItem.id == item_id)
            .options(selectinload(AccessRequestItem.access_request))
        )
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None or item.status != AccessRequestItemStatus.PENDING:
            return None
        item.status = decision
        item.decided_by = decided_by
        item.decided_at = dt.datetime.now(dt.UTC)
        item.decision_reason = decision_reason

        siblings_result = await session.execute(
            select(AccessRequestItem).where(
                AccessRequestItem.access_request_id == item.access_request_id
            )
        )
        siblings = list(siblings_result.scalars().all())
        aggregate = compute_aggregate_status([s.status for s in siblings])

        request = item.access_request
        if request is not None:
            request.status = aggregate

        await session.flush()
        return item
