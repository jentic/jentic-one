"""Repository for Credential CRUD operations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, raiseload
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.shared.db.keyset import apply_desc_keyset
from jentic_one.shared.models.api_identity import slugify_api_field
from jentic_one.shared.models.credentials import StoredCredentialType
from jentic_one.shared.pagination import Cursor

# Secret-material child relationships on ``Credential`` (one row per credential).
_CHILD_RELATIONSHIPS: tuple[str, ...] = (
    "customer_api_key",
    "oauth_client_credential",
    "basic_credential",
    "token_value_credential",
    "oauth_token",
    "sigv4_credential",
    "device_authorization_credential",
)

# The child relationship the broker resolver reads for each stored type.
# ``NO_AUTH`` carries no material; OAuth client/device-flow rows are not read
# at resolution time.
_MATERIAL_RELATIONSHIP: Mapping[str, str] = {
    StoredCredentialType.API_KEY: "customer_api_key",  # pragma: allowlist secret
    StoredCredentialType.BASIC_AUTH: "basic_credential",
    StoredCredentialType.STATIC_BEARER_TOKEN: "token_value_credential",
    StoredCredentialType.SESSION_TOKEN: "token_value_credential",
    StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS: "oauth_token",
    StoredCredentialType.OAUTH2_AUTHORIZATION_CODE: "oauth_token",
    StoredCredentialType.OAUTH2_DEVICE_CODE: "oauth_token",
    StoredCredentialType.OAUTH2_IMPLICIT: "oauth_token",
    StoredCredentialType.AWS_SIGV4: "sigv4_credential",
}

# OAuth grants a human connects interactively; they need a live token.
_INTERACTIVE_OAUTH_TYPES: tuple[str, ...] = (
    StoredCredentialType.OAUTH2_AUTHORIZATION_CODE.value,
    StoredCredentialType.OAUTH2_DEVICE_CODE.value,
)


# Upper bound on IDs bound into one IN (...) clause, well under SQLite's and
# Postgres's host-parameter limits.
_ID_BATCH_SIZE = 500


class CredentialRepository:
    """Data access layer for Credential entities — flush-only, never commits."""

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        credential_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Credential | None:
        if not filters:
            return await session.get(Credential, credential_id)
        stmt = select(Credential).where(Credential.id == credential_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        type: str,
        name: str,
        api_vendor: str,
        created_by: str,
        description: str | None = None,
        api_name: str | None = None,
        api_version: str | None = None,
        catalog_api_id: str | None = None,
        provider: str = "static",
        provider_account_ref: str | None = None,
        server_variables: dict[str, str] | None = None,
        state: str = "connected",
    ) -> Credential:
        credential = Credential(
            type=type,
            name=name,
            api_vendor=api_vendor,
            created_by=created_by,
            description=description,
            api_name=api_name,
            api_version=api_version,
            catalog_api_id=catalog_api_id,
            provider=provider,
            provider_account_ref=provider_account_ref,
            server_variables=server_variables,
            state=state,
        )
        session.add(credential)
        await session.flush()
        return credential

    @staticmethod
    async def list_by_vendor(session: AsyncSession, api_vendor: str) -> list[Credential]:
        """Fetch candidate credential headers for a vendor — no secret material.

        This is the broker's per-call resolution query. Every secret-material
        child relationship ``raise``s on access instead of being loaded (the
        model's default is a ``selectin`` fan-out over every child table), so
        material of candidates the caller filters out is never read. A caller
        that needs material calls :meth:`load_material` on the credential(s)
        it actually uses.

        Widen the prefilter to also match the caller's raw (un-slugified) input so
        historical rows persisted before vendor normalization still surface as
        candidates; the final identity match is normalized in the resolver.

        Rows are ordered by ``id`` so the ambiguity 409's candidate list and the
        id its directive suggests are stable across calls.
        """
        normalized = slugify_api_field(api_vendor)
        result = await session.execute(
            select(Credential)
            .where(Credential.api_vendor.in_({api_vendor, normalized}))
            .options(*(raiseload(getattr(Credential, rel)) for rel in _CHILD_RELATIONSHIPS))
            .order_by(Credential.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def load_material(session: AsyncSession, credentials: Sequence[Credential]) -> None:
        """Populate each credential's type-matching secret-material child.

        Issues one ``WHERE credential_id IN (…)`` query per distinct child
        relationship needed by ``credentials`` and none for ``NO_AUTH``, so a
        single credential costs at most one query that reads only its own row.
        Child relationships not matching a credential's ``type`` are left as
        they were (``raise`` on access for :meth:`list_by_vendor` results).

        Credentials without a child row get ``None`` (as the ``selectin`` default
        would). With duplicate child rows the first one wins, mirroring the
        ``uselist=False`` selectin behaviour.
        """
        owners_by_rel: dict[str, list[Credential]] = defaultdict(list)
        for credential in credentials:
            rel = _MATERIAL_RELATIONSHIP.get(credential.type)
            if rel is not None:
                owners_by_rel[rel].append(credential)

        for rel, owners in owners_by_rel.items():
            prop = Credential.__mapper__.relationships[rel]
            child_cls: Any = prop.mapper.class_
            back = prop.back_populates
            stmt = select(child_cls).where(child_cls.credential_id.in_([c.id for c in owners]))
            if back:
                # The parent is already in the identity map; set it below rather
                # than letting the child's own ``selectin`` back-reference query.
                stmt = stmt.options(lazyload(getattr(child_cls, back)))
            rows = await session.execute(stmt)
            child_by_owner: dict[str, Any] = {}
            for child in rows.scalars():
                child_by_owner.setdefault(child.credential_id, child)
            for owner in owners:
                child = child_by_owner.get(owner.id)
                set_committed_value(owner, rel, child)
                if child is not None and back:
                    set_committed_value(child, back, owner)

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        cursor: Cursor | None = None,
        limit: int = 50,
        vendor: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Credential]:
        """List credentials with keyset pagination (created_at, id)."""
        stmt = select(Credential).order_by(Credential.created_at.desc(), Credential.id.desc())
        if vendor is not None:
            stmt = stmt.where(Credential.api_vendor == vendor)
        stmt = apply_desc_keyset(stmt, Credential.created_at, Credential.id, cursor)
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_header(
        session: AsyncSession,
        credential_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        provider_account_ref: str | None = None,
        active: bool | None = None,
        server_variables: dict[str, str] | None = None,
    ) -> Credential | None:
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return None
        if name is not None:
            credential.name = name
        if provider is not None:
            credential.provider = provider
        if provider_account_ref is not None:
            credential.provider_account_ref = provider_account_ref
        if active is not None:
            credential.active = active
        if server_variables is not None:
            credential.server_variables = server_variables
        await session.flush()
        return credential

    @staticmethod
    async def get_creators_by_ids(
        session: AsyncSession,
        ids: list[str],
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> dict[str, str | None]:
        """Batch-resolve credential IDs to their ``created_by`` (missing/filtered ids omitted)."""
        if not ids:
            return {}
        creators: dict[str, str | None] = {}
        for i in range(0, len(ids), _ID_BATCH_SIZE):
            batch = ids[i : i + _ID_BATCH_SIZE]
            stmt = select(Credential.id, Credential.created_by).where(Credential.id.in_(batch))
            if filters is not None:
                for f in filters:
                    stmt = stmt.where(f)
            result = await session.execute(stmt)
            for row in result:
                creators[row.id] = row.created_by
        return creators

    @staticmethod
    async def get_creators_among(
        session: AsyncSession,
        actor_ids: Sequence[str],
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> set[str]:
        """Return the ids in ``actor_ids`` that created a credential matching ``filters``."""
        if not actor_ids:
            return set()
        stmt = (
            select(Credential.created_by)
            .where(Credential.created_by.in_(list(actor_ids)))
            .distinct()
        )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return {row for row in result.scalars().all() if row is not None}

    @staticmethod
    async def get_registration_display_names(
        session: AsyncSession, ids: list[str]
    ) -> dict[str, str]:
        """Batch-resolve credential IDs to the display name of their OAuth app registration.

        Credentials with no registration (or missing ids) are omitted.
        """
        if not ids:
            return {}
        stmt = (
            select(Credential.id, OAuthAppRegistration.display_name)
            .join(
                OAuthAppRegistration,
                OAuthAppRegistration.id == Credential.oauth_app_registration_id,
            )
            .where(Credential.id.in_(ids))
        )
        result = await session.execute(stmt)
        return {row.id: row.display_name for row in result}

    @staticmethod
    async def set_created_by(
        session: AsyncSession,
        credential_id: str,
        *,
        created_by: str,
    ) -> Credential | None:
        """Re-attribute a credential (and any child material rows) to a creator."""
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return None
        credential.created_by = created_by
        for rel in _CHILD_RELATIONSHIPS:
            child = getattr(credential, rel)
            if child is not None:
                child.created_by = created_by
        await session.flush()
        return credential

    @staticmethod
    async def set_oauth_app_registration(
        session: AsyncSession,
        credential_id: str,
        *,
        registration_id: str,
    ) -> Credential | None:
        """Bind a credential to a shared ``oauth_app_registrations`` row.

        Sanctioned writer for ``credentials.oauth_app_registration_id`` — see
        ``tests/arch/test_oauth_app_registration_invariants.py``.
        """
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return None
        credential.oauth_app_registration_id = registration_id
        await session.flush()
        return credential

    @staticmethod
    async def set_state(session: AsyncSession, credential_id: str, state: str) -> Credential | None:
        """Set a credential's lifecycle state (``pending`` → ``connected`` on a connect)."""
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return None
        credential.state = state
        await session.flush()
        return credential

    @staticmethod
    async def set_type_and_provider(
        session: AsyncSession,
        credential_id: str,
        *,
        type: str,
        provider: str,
        catalog_api_id: str | None = None,
    ) -> Credential | None:
        """Re-type a still-pending connect-session credential once its flow is known."""
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return None
        credential.type = type
        credential.provider = provider
        if catalog_api_id is not None:
            credential.catalog_api_id = catalog_api_id
        await session.flush()
        return credential

    @staticmethod
    async def list_connected_covering(
        session: AsyncSession,
        *,
        vendor: str,
        name: str | None,
        version: str | None,
        account_ref_providers: Collection[str],
        exclude_ids: Sequence[str] = (),
        created_by_in: Sequence[str] | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
        limit: int = 50,
    ) -> list[Credential]:
        """Connected, active credentials whose API scope covers the identity.

        A NULL ``api_name`` / ``api_version`` on the credential is a wildcard;
        a ``None`` reference axis matches any value. ``created_by_in``
        optionally restricts rows to a set of creator IDs (e.g. the target
        agent's owner and the confirming admin, #1838); ``filters`` carries the
        caller's access scoping.

        An interactive OAuth grant (authorization_code / device_code) counts
        as connected only while it can mint a token, matching the redacted
        view's ``connected`` flag: an account reference on one of
        ``account_ref_providers`` (``ProviderRegistry.account_ref_provider_names``),
        or a live token row that is unrevoked and either refreshable or
        unexpired. One whose grant was dropped or revoked needs a reconnect,
        so it is not offered for reuse, even when ``provider_account_ref``
        holds an identity-echo display name.
        """
        now = datetime.now(UTC)
        live_grant = (
            select(OAuthToken.id)
            .where(
                OAuthToken.credential_id == Credential.id,
                OAuthToken.revoked_at.is_(None),
                or_(
                    OAuthToken.encrypted_refresh_token.is_not(None),
                    OAuthToken.expires_at.is_(None),
                    OAuthToken.expires_at > now,
                ),
            )
            .exists()
        )
        stmt = select(Credential).where(
            Credential.api_vendor == vendor,
            Credential.state == "connected",
            Credential.active.is_(True),
            or_(
                Credential.type.not_in(_INTERACTIVE_OAUTH_TYPES),
                and_(
                    Credential.provider.in_(sorted(account_ref_providers)),
                    Credential.provider_account_ref.is_not(None),
                ),
                live_grant,
            ),
        )
        if name is not None:
            stmt = stmt.where(or_(Credential.api_name.is_(None), Credential.api_name == name))
        if version is not None:
            stmt = stmt.where(
                or_(Credential.api_version.is_(None), Credential.api_version == version)
            )
        if exclude_ids:
            stmt = stmt.where(Credential.id.not_in(list(exclude_ids)))
        if created_by_in is not None:
            stmt = stmt.where(Credential.created_by.in_(list(created_by_in)))
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        stmt = stmt.order_by(Credential.created_at.desc(), Credential.id.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def any_covering(
        session: AsyncSession,
        *,
        credential_ids: Sequence[str],
        vendor: str,
        name: str | None,
        version: str | None,
    ) -> bool:
        """Whether any of ``credential_ids`` has an API scope covering the identity.

        Same coverage as :meth:`list_connected_covering` (a NULL credential
        axis is a wildcard; a ``None`` reference axis matches any value), but
        whatever the credential's state.
        """
        if not credential_ids:
            return False
        stmt = select(Credential.id).where(
            Credential.id.in_(list(credential_ids)), Credential.api_vendor == vendor
        )
        if name is not None:
            stmt = stmt.where(or_(Credential.api_name.is_(None), Credential.api_name == name))
        if version is not None:
            stmt = stmt.where(
                or_(Credential.api_version.is_(None), Credential.api_version == version)
            )
        result = await session.execute(stmt.limit(1))
        return result.first() is not None

    @staticmethod
    async def delete(session: AsyncSession, credential_id: str) -> bool:
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return False
        await session.delete(credential)
        await session.flush()
        return True
