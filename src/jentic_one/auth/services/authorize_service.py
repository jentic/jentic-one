"""Authorization code + PKCE flow service."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import secrets
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import structlog

from jentic_one.admin.core.permissions import ALL_PERMISSIONS
from jentic_one.admin.repos import (
    AuthorizationCodeRepository,
    ExternalIdentityRepository,
    UserPermissionGrantRepository,
    UserRepository,
)
from jentic_one.auth.core.id_token import issue_id_token
from jentic_one.auth.core.idp import (
    AdmissionDecision,
    IdpAdapter,
    IdpClaims,
    build_idp_adapter,
    get_admission_policy,
    get_default_idp_grants,
)
from jentic_one.auth.services.errors import InvalidGrantError, UserNotAdmittedError
from jentic_one.auth.services.token_service import TokenService
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db import DatabaseIntegrityError
from jentic_one.shared.models import ActorType, InviteState


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


_logger = structlog.get_logger(__name__)


def _valid_grants(permissions: list[str]) -> list[str]:
    """Keep only permission names present in the catalogue, dropping unknowns.

    A default-grants provider is deployment-configured, so a typo or a scope that
    no longer exists must not be able to fail an otherwise-valid login. Unknown
    names are logged once and skipped.
    """
    known = [p for p in permissions if p in ALL_PERMISSIONS]
    unknown = [p for p in permissions if p not in ALL_PERMISSIONS]
    if unknown:
        _logger.warning("idp_default_grants_unknown_dropped", unknown=sorted(set(unknown)))
    return known


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac_mod.compare_digest(computed, code_challenge)


class AuthorizeService:
    """Handles AuthCode+PKCE flow: code issuance, exchange, and IdP federation."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._token_svc = TokenService(ctx)

    @property
    def _auth_config(self) -> AuthConfig:
        return self._ctx.config.auth

    def _get_idp_adapter(self) -> IdpAdapter | None:
        return build_idp_adapter(self._auth_config.idp)

    def get_authorize_redirect_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
    ) -> str | None:
        """Get the upstream IdP authorization URL, or None if local-only."""
        adapter = self._get_idp_adapter()
        if adapter is None:
            return None
        return adapter.authorize_url(state=state, nonce=nonce, redirect_uri=redirect_uri)

    async def handle_idp_callback(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        original_redirect_uri: str,
        code_challenge: str,
        scopes: str,
        nonce: str | None,
    ) -> str:
        """Handle IdP callback: exchange upstream code, map identity, issue auth code.

        Returns the platform authorization code.
        """
        platform_code, _ = await self.handle_idp_callback_with_email(
            code=code,
            redirect_uri=redirect_uri,
            client_id=client_id,
            original_redirect_uri=original_redirect_uri,
            code_challenge=code_challenge,
            scopes=scopes,
            nonce=nonce,
        )
        return platform_code

    async def handle_idp_callback_with_email(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        original_redirect_uri: str,
        code_challenge: str,
        scopes: str,
        nonce: str | None,
    ) -> tuple[str, str]:
        """Handle IdP callback and return both platform code and user email.

        Returns (platform_authorization_code, user_email).
        Used by consent flow to display user identity on the consent page.
        """
        adapter = self._get_idp_adapter()
        if adapter is None:
            raise InvalidGrantError("No external IdP configured")

        userinfo = await adapter.exchange_code(code, redirect_uri=redirect_uri)
        claims = adapter.map_claims(userinfo)
        user_id = await self._resolve_or_create_user(claims)

        platform_code = await self._issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=original_redirect_uri,
            code_challenge=code_challenge,
            scopes=scopes,
            nonce=nonce,
        )
        return platform_code, claims.email

    async def resolve_idp_user(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> tuple[str, str]:
        """Exchange an upstream IdP code and resolve the local user.

        Returns (user_id, user_email) without minting an authorization code.
        Used by the consent flow to defer code minting until after approval.
        """
        adapter = self._get_idp_adapter()
        if adapter is None:
            raise InvalidGrantError("No external IdP configured")

        userinfo = await adapter.exchange_code(code, redirect_uri=redirect_uri)
        claims = adapter.map_claims(userinfo)
        user_id = await self._resolve_or_create_user(claims)
        return user_id, claims.email

    async def issue_authorization_code(
        self,
        *,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scopes: str = "openid",
        nonce: str | None = None,
    ) -> str:
        """Issue an authorization code for a locally-authenticated user."""
        return await self._issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scopes,
            nonce=nonce,
        )

    async def _issue_authorization_code(
        self,
        *,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scopes: str,
        nonce: str | None,
    ) -> str:
        code_plain = secrets.token_urlsafe(32)
        code_hash = _hash_code(code_plain)
        ttl = self._auth_config.auth_code_ttl_seconds

        async with self._ctx.admin_db.transaction() as session:
            auth_code = await AuthorizationCodeRepository.create(
                session,
                code_hash=code_hash,
                user_id=user_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                scopes=scopes,
                nonce=nonce,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
                created_by=user_id,
            )
            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.TOKEN,
                target_id=auth_code.id,
                actor_type=ActorType.USER,
                actor_id=user_id,
                reason="authorization code issued",
                origin=None,
            )

        return code_plain

    async def precheck_auth_code(self, code: str) -> None:
        """Cheap read-only validity check on an auth code before spending argon2.

        Unauthenticated callers hit ``/oauth/token`` with garbage client secrets,
        and confidential-client verification runs argon2id (~25 ms + 64 MiB per
        verify, with a dummy-hash timing equalizer for unknown client_ids). That
        turns the endpoint into a memory/CPU amplifier — a few hundred bytes of
        request → 64 MiB of server work — before the auth code is even inspected.
        This shortcut peeks the code hash without ``FOR UPDATE`` and fails fast
        on a bad/consumed/expired code, so junk requests never reach argon2.
        """
        code_hash = _hash_code(code)
        async with self._ctx.admin_db.session() as session:
            auth_code = await AuthorizationCodeRepository.get_by_hash(session, code_hash)
        if auth_code is None:
            raise InvalidGrantError("authorization code not found")
        if auth_code.consumed_at is not None:
            raise InvalidGrantError("authorization code already used")
        if auth_code.expires_at <= datetime.now(UTC):
            raise InvalidGrantError("authorization code expired")

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        oauth_client_id: str | None = None,
    ) -> tuple[str, str, str]:
        """Exchange auth code + PKCE verifier for tokens.

        Returns (access_token, refresh_token, id_token).
        """
        code_hash = _hash_code(code)
        now = datetime.now(UTC)

        async with self._ctx.admin_db.transaction() as session:
            auth_code = await AuthorizationCodeRepository.get_by_hash(
                session, code_hash, for_update=True
            )

            if auth_code is None:
                raise InvalidGrantError("authorization code not found")

            if auth_code.consumed_at is not None:
                raise InvalidGrantError("authorization code already used")

            if auth_code.expires_at <= now:
                raise InvalidGrantError("authorization code expired")

            if auth_code.client_id != client_id:
                raise InvalidGrantError("client_id mismatch")

            if auth_code.redirect_uri != redirect_uri:
                raise InvalidGrantError("redirect_uri mismatch")

            if not _verify_pkce(code_verifier, auth_code.code_challenge):
                raise InvalidGrantError("PKCE verification failed")

            await AuthorizationCodeRepository.consume(session, auth_code.id, now)

            user = await UserRepository.get_by_id(session, auth_code.user_id)

            if user is not None:
                await record_audit(
                    session,
                    action=AuditAction.LOGIN,
                    target_type=AuditTargetType.SESSION,
                    target_id=user.id,
                    actor_type=ActorType.USER,
                    actor_id=user.id,
                    reason="authorization code exchange",
                    origin=None,
                )

        if user is None:
            raise InvalidGrantError("user not found")

        scopes = auth_code.scopes.split() if auth_code.scopes else ["openid"]
        access_token, refresh_token = await self._token_svc.issue_pair(
            user.id, ActorType.USER, scopes, oauth_client_id=oauth_client_id
        )

        id_token = issue_id_token(
            self._auth_config,
            sub=user.id,
            email=user.email,
            aud=client_id,
            nonce=auth_code.nonce,
        )

        return access_token, refresh_token, id_token

    async def _resolve_or_create_user(self, claims: IdpClaims) -> str:
        """Resolve external identity to existing user or create a new one.

        Auto-links to an existing account by email only when the IdP asserts
        email_verified=true. When the email is unverified and already belongs to
        a local account, the login is rejected (fail closed) rather than linked
        or silently creating a duplicate — emails are unique, so a duplicate is
        impossible and a takeover via unverified email must not be allowed.

        Handles the race condition where concurrent callbacks for the same
        external_subject both pass the initial lookup — the UniqueConstraint
        on (provider, external_subject) rejects the second insert, which is
        caught and retried as a lookup.
        """
        provider = self._auth_config.idp.provider

        async with self._ctx.admin_db.transaction() as session:
            ext_id = await ExternalIdentityRepository.get_by_provider_subject(
                session, provider, claims.external_subject
            )
            if ext_id is not None:
                return ext_id.user_id

        try:
            async with self._ctx.admin_db.transaction() as session:
                existing_user = await UserRepository.get_by_email(session, claims.email)
                if existing_user is not None:
                    if not claims.email_verified:
                        raise InvalidGrantError(
                            "Email is not verified by the identity provider and is "
                            "already associated with an existing account"
                        )
                    await ExternalIdentityRepository.create(
                        session,
                        provider=provider,
                        external_subject=claims.external_subject,
                        user_id=existing_user.id,
                        email=claims.email,
                        created_by=existing_user.id,
                    )
                    await record_audit(
                        session,
                        action=AuditAction.CREATE,
                        target_type=AuditTargetType.USER,
                        target_id=existing_user.id,
                        actor_type=ActorType.USER,
                        actor_id=existing_user.id,
                        reason=f"linked external identity ({provider})",
                        origin=None,
                    )
                    return existing_user.id

                # Brand-new (never-seen) verified email: consult the deployment's
                # admission policy. Default (open) admits any verified email — the
                # historical behaviour. A stricter policy (invite-only, domain-
                # gated, …) can decline via set_admission_policy(); the already-
                # linked and existing-account paths above are never gated. On
                # reject we leave this transaction WITHOUT writing (a rollback
                # would discard a reject audit), then audit + raise below.
                if get_admission_policy()(claims) is not AdmissionDecision.ADMIT_AND_CREATE:
                    await self._audit_admission_rejected(claims, provider)
                    raise UserNotAdmittedError(
                        "This account is not permitted to sign in to this deployment"
                    )

                new_user = await UserRepository.create(
                    session,
                    email=claims.email,
                    first_name=claims.first_name,
                    last_name=claims.last_name,
                    active=True,
                    auth_provider=provider,
                    external_subject_id=claims.external_subject,
                    invite_state=InviteState.ACCEPTED,
                    created_by="self",
                )
                await ExternalIdentityRepository.create(
                    session,
                    provider=provider,
                    external_subject=claims.external_subject,
                    user_id=new_user.id,
                    email=claims.email,
                    created_by=new_user.id,
                )
                # Baseline permissions for a brand-new IdP user (default: none).
                # Applied only here, at creation — existing/linked accounts above
                # are never touched. Written in this same transaction so the user
                # and their grants land atomically. Unknown scope names are
                # dropped defensively so a misconfigured list can't 500 the login.
                default_grants = _valid_grants(get_default_idp_grants()(claims))
                if default_grants:
                    await UserPermissionGrantRepository.set_permissions(
                        session,
                        new_user.id,
                        permissions=set(default_grants),
                        granted_by=new_user.id,
                        created_by=new_user.id,
                    )
                await record_audit(
                    session,
                    action=AuditAction.CREATE,
                    target_type=AuditTargetType.USER,
                    target_id=new_user.id,
                    actor_type=ActorType.USER,
                    actor_id=new_user.id,
                    after={
                        "email": claims.email,
                        "auth_provider": provider,
                        "granted_permissions": sorted(default_grants),
                    },
                    reason="provisioned via external IdP",
                    origin=None,
                )
                return new_user.id
        except DatabaseIntegrityError:
            pass

        async with self._ctx.admin_db.transaction() as session:
            ext_id = await ExternalIdentityRepository.get_by_provider_subject(
                session, provider, claims.external_subject
            )
            if ext_id is not None:
                return ext_id.user_id
            raise InvalidGrantError("concurrent identity creation failed")

    async def _audit_admission_rejected(self, claims: IdpClaims, provider: str) -> None:
        """Record a rejected external-IdP login in its own committed transaction.

        Kept separate from the provisioning transaction because that transaction
        rolls back when the reject is raised — inlining the audit there would
        discard it.
        """
        async with self._ctx.admin_db.transaction() as session:
            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.USER,
                target_id=claims.email,
                actor_type=ActorType.USER,
                actor_id=claims.email,
                reason=f"external IdP login not admitted ({provider})",
                origin=None,
            )
