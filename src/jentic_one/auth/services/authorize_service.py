"""Authorization code + PKCE flow service."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import secrets
from base64 import urlsafe_b64encode
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.permissions import (
    AGENTS_WRITE,
    ALL_PERMISSIONS,
    compute_effective,
)
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentRepository,
    AuthorizationCodeRepository,
    ExternalIdentityRepository,
    OAuthClientGrantRepository,
    OAuthClientRepository,
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
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import (
    ConsentAgentNotEligibleError,
    InvalidGrantError,
    UserNotAdmittedError,
)
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.services.schemas.agents import AgentCreatePayload
from jentic_one.auth.services.token_service import (
    TokenService,
    resolve_effective_scopes,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.owner_status import log_agent_owner_inactive
from jentic_one.shared.auth.permission_catalog import OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.config import AuthConfig, resolved_auth_base_url
from jentic_one.shared.context import Context
from jentic_one.shared.db import DatabaseIntegrityError
from jentic_one.shared.models import (
    ActorStatus,
    ActorType,
    InviteState,
    OAuthClientApprovalStatus,
)
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus


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


@dataclass(frozen=True, slots=True)
class AgentConsentOption:
    """One row of the agent-picker consent page.

    ``permissions`` is the agent's *live* permission set (its current
    ``actor_permission_grants``) — the consent page intersects it with the
    requested OAuth2 scopes per candidate, and the submit path recomputes
    server-side (the browser's selection is never trusted for scope math).
    """

    id: str
    name: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class PendingAgentRef:
    """The newest PENDING agent owned by the consenting user (P4 hybrid).

    Carries only what the awaiting-approval page needs — the id the status
    blob is bound to and the display name. Deliberately no scopes: a pending
    agent has no live grants until an admin approves it.
    """

    id: str
    name: str


def candidate_agent_scopes(
    requested: list[str],
    allowlist: frozenset[str] | None,
) -> list[str]:
    """The scopes an agent-bound grant could carry: requested ∩ client allowlist.

    OIDC passthrough scopes (``openid`` and friends) are stripped first:
    agent-bound grants carry no OIDC identity, so they must never enter the
    granted set. Order follows the request.

    This is the half of the grant-scope rule that does not depend on which
    agent the user picks, so the consent page shows exactly this list per
    agent option and :func:`effective_agent_scopes` starts from it — one
    function, so the page can never advertise a scope the grant would drop.
    """
    candidates = [s for s in requested if s not in OIDC_PASSTHROUGH_SCOPES]
    if allowlist is None:
        return candidates
    return [s for s in candidates if s in allowlist]


def effective_agent_scopes(
    requested: list[str],
    allowlist: frozenset[str] | None,
    agent_permissions: frozenset[str],
) -> list[str]:
    """The grant-scope intersection: requested ∩ client allowlist ∩ the agent's
    live permissions.

    Named for what it returns — a set of OAuth2 scopes for the grant row — even
    though the third operand is read from ``actor_permission_grants``.

    The first two operands come from :func:`candidate_agent_scopes` (which the
    consent page also calls), so the page and the grant row agree by
    construction; the agent's live permission set is the ceiling applied on
    top. Order follows the request.
    """
    candidates = candidate_agent_scopes(requested, allowlist)
    return [s for s in candidates if s in agent_permissions]


@dataclass(frozen=True, slots=True)
class AgentConsentApproval:
    """Outcome of :meth:`AuthorizeService.approve_agent_consent`.

    Either ``error`` is set (the browser-facing ``/error?error=…`` code; no
    grant was minted) or ``grant_id`` + ``scopes`` describe the minted grant.
    """

    grant_id: str | None = None
    scopes: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentAgentCreateResult:
    """Outcome of :meth:`AuthorizeService.create_agent_from_consent`.

    - ``reenter_consent`` — re-enter the consent page (an ACTIVE agent was
      created, an agent already existed, or a parallel submit won the slot);
    - ``await_approval`` — a PENDING agent was created (``agent_id`` /
      ``agent_name``); the caller parks the user on the awaiting page;
    - ``error`` — nothing was created; ``error`` is the browser-facing
      ``/error?error=…`` code.
    """

    outcome: Literal["reenter_consent", "await_approval", "error"]
    error: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None


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

    def _effective_idp_issuer(self, claims: IdpClaims) -> str:
        """Resolve the canonical IdP issuer for external-identity keying."""
        if claims.issuer:
            return claims.issuer.rstrip("/")
        adapter = self._get_idp_adapter()
        if adapter is not None and adapter.issuer:
            return adapter.issuer
        if self._auth_config.idp.issuer:
            return self._auth_config.idp.issuer.rstrip("/")
        if self._auth_config.idp.provider == "google":
            return "https://accounts.google.com"
        return self._auth_config.idp.provider

    def get_authorize_redirect_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        code_challenge: str | None = None,
    ) -> str | None:
        """Get the upstream IdP authorization URL, or None if local-only."""
        adapter = self._get_idp_adapter()
        if adapter is None:
            return None
        return adapter.authorize_url(
            state=state,
            nonce=nonce,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
        )

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
        idp_code_verifier: str | None = None,
        idp_nonce: str | None = None,
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
            idp_code_verifier=idp_code_verifier,
            idp_nonce=idp_nonce,
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
        idp_code_verifier: str | None = None,
        idp_nonce: str | None = None,
    ) -> tuple[str, str]:
        """Handle IdP callback and return both platform code and user email.

        Returns (platform_authorization_code, user_email).
        Used by consent flow to display user identity on the consent page.
        """
        adapter = self._get_idp_adapter()
        if adapter is None:
            raise InvalidGrantError("No external IdP configured")

        userinfo = await adapter.exchange_code(
            code,
            redirect_uri=redirect_uri,
            code_verifier=idp_code_verifier,
            nonce=idp_nonce,
        )
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
        idp_code_verifier: str | None = None,
        idp_nonce: str | None = None,
    ) -> tuple[str, str]:
        """Exchange an upstream IdP code and resolve the local user.

        Returns (user_id, user_email) without minting an authorization code.
        Used by the consent flow to defer code minting until after approval.
        """
        adapter = self._get_idp_adapter()
        if adapter is None:
            raise InvalidGrantError("No external IdP configured")

        userinfo = await adapter.exchange_code(
            code,
            redirect_uri=redirect_uri,
            code_verifier=idp_code_verifier,
            nonce=idp_nonce,
        )
        claims = adapter.map_claims(userinfo)
        user_id = await self._resolve_or_create_user(claims)
        return user_id, claims.email

    async def exchange_idp_code_for_claims(
        self,
        *,
        code: str,
        redirect_uri: str,
        idp_code_verifier: str | None = None,
        idp_nonce: str | None = None,
    ) -> IdpClaims:
        """Exchange an upstream IdP code and return the claims *without* provisioning.

        Third-party consent flows must not provision a local account until the
        user has actually approved: an accidentally-triggered /authorize on a
        registered third-party client should be able to end in a "Deny" without
        leaving behind an account and an external-identity link the user never
        agreed to. Consent-approve callers then hand the returned claims to
        :meth:`provision_from_claims`.
        """
        adapter = self._get_idp_adapter()
        if adapter is None:
            raise InvalidGrantError("No external IdP configured")

        userinfo = await adapter.exchange_code(
            code,
            redirect_uri=redirect_uri,
            code_verifier=idp_code_verifier,
            nonce=idp_nonce,
        )
        return adapter.map_claims(userinfo)

    async def provision_from_claims(self, claims: IdpClaims) -> str:
        """Resolve-or-create a local user from IdP claims. Returns the user_id."""
        return await self._resolve_or_create_user(claims)

    async def resolve_existing_user_id(self, claims: IdpClaims) -> str | None:
        """Read-only user resolution for the agent-picker consent page.

        The deferred-provisioning contract holds — rendering the consent page
        must not create a user row — but the agent picker needs to know whose
        agents to list. Resolution mirrors ``_resolve_or_create_user``'s
        lookup arms without the create: external-identity link first, then
        email match only when the IdP asserts ``email_verified`` (an
        unverified email must not expose another account's agent list).
        """
        issuer = self._effective_idp_issuer(claims)
        async with self._ctx.admin_db.session() as session:
            ext_id = await ExternalIdentityRepository.get_by_issuer_subject(
                session, issuer, claims.external_subject
            )
            if ext_id is None:
                ext_id = await self._adoptable_unscoped_link(
                    session, self._auth_config.idp.provider, issuer, claims.external_subject
                )
            if ext_id is not None:
                return ext_id.user_id
            if claims.email_verified:
                user = await UserRepository.get_by_email(session, claims.email)
                if user is not None:
                    return user.id
        return None

    async def list_consentable_agents(self, user_id: str) -> list[AgentConsentOption]:
        """The consenting user's own ``status='active'`` agents + live scopes.

        Only admin-approved (active) agents are bindable; pending,
        denied, disabled, and archived agents never appear on the picker.
        """
        async with self._ctx.admin_db.session() as session:
            # limit=1000: the design specifies no picker ceiling, but the
            # repo API is limit-shaped. The bound is explicit and generous —
            # beyond it the newest-first (created_at DESC) order deterministically
            # drops the *oldest* agents from both render and submit (the same
            # call validates the selection, so there is no render/validate skew).
            agents = await AgentRepository.list_by_owner(
                session,
                user_id,
                limit=1000,
                filters=[Agent.status == ActorStatus.ACTIVE.value],
            )
            # One batch query for every candidate's live permissions, which
            # avoids a per-agent actor_permission_grants round-trip — run twice,
            # because the submit path re-runs this predicate.
            grants = await ActorPermissionGrantRepository.list_for_actors(
                session, [agent.id for agent in agents], actor_type=ActorType.AGENT.value
            )
            permissions_by_agent: dict[str, set[str]] = {}
            for grant in grants:
                permissions_by_agent.setdefault(grant.actor_id, set()).add(grant.permission)
            return [
                AgentConsentOption(
                    id=agent.id,
                    name=agent.name,
                    permissions=frozenset(permissions_by_agent.get(agent.id, set())),
                )
                for agent in agents
            ]

    async def owner_has_any_agents(self, user_id: str) -> bool:
        """Whether the user owns ANY agent row, in any status.

        The inline create-agent arm (P4) keys on this, NOT on the active-only
        picker predicate above: a user whose agents were all disabled or
        archived by an admin has zero *consentable* agents but is not a
        first-run user — offering the create form there would let the owner
        mint a fresh ACTIVE agent mid-flow and sidestep the admin's action.
        "First run" means zero agent rows, ever.
        """
        async with self._ctx.admin_db.session() as session:
            agents = await AgentRepository.list_by_owner(session, user_id, limit=1)
            return bool(agents)

    async def user_can_create_active_agent(self, user_id: str) -> bool:
        """Whether the consenting user would pass POST /agents' ``agents:write`` gate.

        The inline consent creation must not out-privilege the SPA door
        (security review on P4): every authenticated agent-creation surface
        requires ``agents:write``, so the mid-flow arm split keys on the SAME
        math the web gate applies — the user's assigned permission grants
        expanded through the static implication map (``org:admin`` implies
        ``agents:write`` there, exactly as ``get_current_identity``'s
        ``compute_effective`` + org:admin check would admit that caller).
        Evaluated server-side against the consent handle's subject; the
        browser asserts nothing.
        """
        async with self._ctx.admin_db.session() as session:
            grants = await UserPermissionGrantRepository.get_grants_for_user(session, user_id)
        effective = compute_effective({g.permission for g in grants})
        return AGENTS_WRITE in effective

    async def newest_pending_agent(self, user_id: str) -> PendingAgentRef | None:
        """The user's newest ``status='pending'`` agent, or None.

        The consent page's awaiting-approval arm (P4 hybrid) keys on this: a
        user whose only agents sit in PENDING is mid-approval (the inline
        create's pending arm, or the ``/register`` queue), so re-entering the
        flow parks them on the awaiting page for the newest one instead of a
        dead end. Newest-first mirrors the picker's ordering.
        """
        async with self._ctx.admin_db.session() as session:
            agents = await AgentRepository.list_by_owner(
                session,
                user_id,
                limit=1,
                filters=[Agent.status == ActorStatus.PENDING.value],
            )
        if not agents:
            return None
        return PendingAgentRef(id=agents[0].id, name=agents[0].name)

    async def get_agent_status(self, agent_id: str) -> str | None:
        """The raw lifecycle status of an agent row, or None when absent.

        Read path for the anonymous pending-agent status poll: the caller
        (web layer) collapses it into the tri-state and never exposes more —
        the blob gating the poll is bound to one agent id, so this can only
        ever be asked about the agent the flow itself parked on.
        """
        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)
        return None if agent is None else agent.status

    async def approve_agent_consent(
        self,
        *,
        user_id: str,
        client_id: str,
        client_name: str,
        allowed_scopes: list[str] | None,
        agent_id: str | None,
        scope: str,
    ) -> AgentConsentApproval:
        """Validate the agent picked on the consent page and mint its grant.

        The browser's selection is never trusted: the agent must be one of
        the consenting user's own ACTIVE agents (the picker's own predicate),
        and the requested ∩ allowlist ∩ agent-permissions scope intersection
        is recomputed here. The grant row (+ audit + ``oauth_grant.created``)
        is minted through :class:`OAuthGrantService`. Every failure returns an
        ``error`` code and mints nothing.
        """
        grant_service = OAuthGrantService(self._ctx)
        if not agent_id:
            _logger.warning("oauth_consent_agent_missing", client_id=client_id)
            return AgentConsentApproval(error="invalid_agent_selection")

        options = await self.list_consentable_agents(user_id)
        selected = next((o for o in options if o.id == agent_id), None)
        if selected is None:
            # Not the user's own active agent — covers unknown ids, other users'
            # agents, and pending/disabled/archived agents in one predicate.
            _logger.warning("oauth_consent_agent_invalid", client_id=client_id)
            return AgentConsentApproval(error="invalid_agent_selection")

        requested = [s.strip() for s in scope.split() if s.strip()]
        allowlist = frozenset(allowed_scopes) if allowed_scopes is not None else None
        effective = effective_agent_scopes(requested, allowlist, selected.permissions)
        if not effective:
            _logger.warning(
                "oauth_consent_no_grantable_scopes", client_id=client_id, agent_id=agent_id
            )
            return AgentConsentApproval(error="no_grantable_scopes")

        try:
            grant_id = await grant_service.create_grant(
                user_id=user_id,
                oauth_client_id=client_id,
                agent_id=selected.id,
                scopes=effective,
                client_name=client_name,
            )
        except ConsentAgentNotEligibleError:
            # The mint-time lock + re-check refused: the agent was transferred,
            # archived, or disabled between the picker validation above and the
            # grant write. Same user-facing posture as a failed picker
            # validation — the human error page, never a code redirect or a 500.
            _logger.warning(
                "oauth_consent_agent_invalid_at_mint", client_id=client_id, agent_id=agent_id
            )
            return AgentConsentApproval(error="invalid_agent_selection")
        return AgentConsentApproval(grant_id=grant_id, scopes=tuple(effective))

    async def create_agent_from_consent(
        self,
        *,
        claims: IdpClaims | None,
        local_user_id: str | None,
        user_email: str,
        origin: Origin,
        agent_name: str,
        client_id: str,
        claim_create_slot: Callable[[], Awaitable[bool]],
    ) -> ConsentAgentCreateResult:
        """Create the consenting user's first agent from the consent page.

        The caller has already verified + burned the signed create blob,
        re-validated the consent handle and the client approval+active gate,
        and validated ``agent_name``. This runs the workflow:

        1. resolve the user — a local-login handle names it; an IdP handle is
           resolved read-only and, when deferred provisioning left no row,
           provisioned here (an affirmative user action, unlike rendering);
        2. re-check the zero-agents predicate — an agent appearing in between
           creates nothing;
        3. claim the per-subject create slot (``claim_create_slot``) — exactly
           one of N parallel submits proceeds;
        4. decide the arm with the same effective-permission math as POST
           /agents' ``agents:write`` gate — ACTIVE when the user holds it,
           otherwise PENDING (the POST /register posture);
        5. create the agent through :class:`AgentService` (owner = the
           consenting user, default agent permissions, same audit + event).

        ``claim_create_slot`` is injected rather than reached from
        ``self._ctx`` because the atomic marker it sets lives in the app's
        shared auth-state backend (``app.state.auth_state_backend``), which
        ``Context`` does not expose; see
        :func:`jentic_one.auth.web.consent_state.claim_agent_create_slot` for
        the only production implementation. Keeping it a parameter also lets
        the workflow be exercised without a web layer.
        """
        agent_service = AgentService(self._ctx)
        if local_user_id:
            user_id = local_user_id
        else:
            resolved = await self.resolve_existing_user_id(claims) if claims else None
            if resolved is None:
                if claims is None:
                    # The handle's subject key guarantees claims exist on this
                    # arm; belt and braces for a malformed handle.
                    _logger.warning("oauth_consent_missing_claims", client_id=client_id)
                    return ConsentAgentCreateResult(outcome="error", error="invalid_consent")
                # Deferred provisioning: same admission policy + audit as the
                # consent approve arm — never at render.
                try:
                    resolved = await self.provision_from_claims(claims)
                except UserNotAdmittedError:
                    _logger.warning("oauth_user_not_admitted", client_id=client_id)
                    return ConsentAgentCreateResult(outcome="error", error="access_denied")
                except InvalidGrantError:
                    _logger.warning("oauth_provision_failed", client_id=client_id, exc_info=True)
                    return ConsentAgentCreateResult(outcome="error", error="server_error")
            user_id = resolved

        # Idempotency/race re-check: if an agent appeared between the form render
        # and this submit (another tab, an admin, a parallel submit), create
        # nothing — just re-enter consent. The predicate is ownership of ANY
        # agent row, in any status, matching the render arm: a user whose agents
        # were disabled or archived by an admin is not first-run, and this
        # submit must not mint them a fresh ACTIVE agent past that action.
        if await self.owner_has_any_agents(user_id):
            _logger.info(
                "oauth_consent_agent_create_skipped_existing", client_id=client_id, user_id=user_id
            )
            return ConsentAgentCreateResult(outcome="reenter_consent")

        # The plain read above cannot see a parallel submit that has not
        # committed yet — the atomic per-subject slot claim can (security
        # review): exactly one of N concurrent submits proceeds to create.
        if not await claim_create_slot():
            _logger.info(
                "oauth_consent_agent_create_lost_race", client_id=client_id, user_id=user_id
            )
            return ConsentAgentCreateResult(outcome="reenter_consent")

        # The hybrid arm split (security review): decided HERE, after the
        # subject is provisioned, with the same effective-permission math as
        # POST /agents' agents:write gate — this public mid-flow door must
        # not out-privilege the SPA's.
        can_create_active = await self.user_can_create_active_agent(user_id)
        create_status = ActorStatus.ACTIVE if can_create_active else ActorStatus.PENDING
        # Owner is ALWAYS the consenting user resolved from the server-side
        # handle — the form carries no owner input. permissions=None applies the
        # platform's DEFAULT_AGENT_PERMISSIONS on the ACTIVE arm, exactly like the
        # SPA path (the PENDING arm defers scopes to approve(), exactly like
        # /register); the service records the same REGISTER audit +
        # agent.created event either way.
        identity = Identity(
            sub=user_id,
            email=user_email,
            actor_type=ActorType.USER,
            origin=origin,
        )
        try:
            view = await agent_service.create(
                AgentCreatePayload(name=agent_name, description=None, permissions=None),
                owner_id=user_id,
                identity=identity,
                status=create_status,
            )
        except DatabaseIntegrityError:
            # The owner FK refused (user row vanished mid-flow) — the shared
            # browser-facing error, never a raw 500.
            _logger.warning("oauth_consent_agent_create_failed", client_id=client_id)
            return ConsentAgentCreateResult(outcome="error", error="server_error")
        _logger.info(
            "oauth_consent_agent_created",
            client_id=client_id,
            agent_id=view.id,
            user_id=user_id,
            status=create_status.value,
        )
        if can_create_active:
            return ConsentAgentCreateResult(outcome="reenter_consent")
        return ConsentAgentCreateResult(
            outcome="await_approval", agent_id=view.id, agent_name=view.name
        )

    async def issue_authorization_code(
        self,
        *,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scopes: str = "openid",
        nonce: str | None = None,
        grant_id: str | None = None,
    ) -> str:
        """Issue an authorization code for a locally-authenticated user."""
        return await self._issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scopes,
            nonce=nonce,
            grant_id=grant_id,
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
        grant_id: str | None = None,
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
                grant_id=grant_id,
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

    async def record_consent_decision(
        self,
        *,
        user_id: str,
        oauth_client_id: str,
        approved: bool,
        scopes: str,
    ) -> None:
        """Audit a user's approve/deny decision on the OAuth consent screen.

        Without this record the audit trail would contain no record of *which*
        user consented to *which* client
        with *which* scopes — impossible to reconstruct after the fact.
        """
        async with self._ctx.admin_db.transaction() as session:
            await record_audit(
                session,
                action=AuditAction.APPROVE if approved else AuditAction.DENY,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=oauth_client_id,
                actor_type=ActorType.USER,
                actor_id=user_id,
                after={"scopes": scopes, "oauth_client_id": oauth_client_id},
                reason="oauth consent approved" if approved else "oauth consent denied",
                origin=None,
            )

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

    async def _redirect_uri_currently_registered(
        self, session: AsyncSession, *, client_id: str, redirect_uri: str
    ) -> bool:
        """True iff ``redirect_uri`` is in the client's CURRENT redirect set.

        Platform clients read from config, registered clients from the live
        row. A missing row fails closed — a code was minted for this
        client_id, so absence means the registration vanished mid-flow.
        """
        for pc in self._ctx.config.auth.platform_clients:
            if pc.client_id == client_id:
                return redirect_uri in pc.redirect_uris
        client = await OAuthClientRepository.get_by_client_id(session, client_id)
        return client is not None and redirect_uri in client.redirect_uris

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        oauth_client_id: str | None = None,
        issuer: str | None = None,
    ) -> tuple[str, str, str | None, list[str]]:
        """Exchange auth code + PKCE verifier for tokens.

        ``issuer`` is the ``iss`` stamped on the ``id_token``; the web layer
        passes the same request-scoped base URL the discovery document
        advertises, so OIDC clients' issuer check matches. When omitted it
        falls back to the request-less ``resolved_auth_base_url``.

        Returns (access_token, refresh_token, id_token, scopes). ``scopes`` is
        the effective set the minted access token will actually enforce,
        reported per RFC 6749 §5.1: for grant-bearing (agent-channel) codes it
        is recomputed at exchange time the way the live resolvers enforce it
        (agent live grants ∩ client ceiling ∩ consent-grant scopes, via
        :func:`resolve_effective_scopes`); for plain codes it is the
        authorize-time snapshot. Grant-bearing codes mint actor=AGENT tokens
        bound to the consent grant and return ``id_token=None`` (D11 — no OIDC
        identity on the agent channel); plain codes keep the act-as-user path
        with an id_token.
        """
        code_hash = _hash_code(code)
        now = datetime.now(UTC)
        grant_id: str | None = None
        grant_agent_id: str | None = None
        grant_scopes: list[str] = []
        grant_effective_scopes: list[str] = []

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

            # The code row pins the authorize-time value, but an admin may
            # have narrowed the client's redirect set inside the code TTL —
            # re-validate against the client's *live* set so a mid-flow
            # narrowing invalidates in-flight codes (review-1246 F6).
            if not await self._redirect_uri_currently_registered(
                session, client_id=client_id, redirect_uri=redirect_uri
            ):
                raise InvalidGrantError("redirect_uri is no longer registered for this client")

            if not _verify_pkce(code_verifier, auth_code.code_challenge):
                raise InvalidGrantError("PKCE verification failed")

            await AuthorizationCodeRepository.consume(session, auth_code.id, now)

            if auth_code.grant_id is not None:
                # Grant-channel exchange: every leg is re-checked at
                # exchange time and fails closed with invalid_grant — the
                # consent-time snapshot is not trusted across the code TTL.
                grant = await OAuthClientGrantRepository.get_by_id(session, auth_code.grant_id)
                if grant is None or grant.status != OAuthGrantStatus.ACTIVE.value:
                    raise InvalidGrantError("consent grant is not active")
                if grant.oauth_client_id != client_id:
                    raise InvalidGrantError("consent grant client mismatch")
                oauth_client = await OAuthClientRepository.get_by_client_id(session, client_id)
                if (
                    oauth_client is None
                    or not oauth_client.active
                    or oauth_client.approval_status != OAuthClientApprovalStatus.APPROVED.value
                ):
                    raise InvalidGrantError("issuing OAuth client is not active")
                found = await AgentRepository.get_with_owner_active(session, grant.agent_id)
                agent, owner_active = found if found is not None else (None, False)
                if agent is None or agent.status != ActorStatus.ACTIVE.value:
                    raise InvalidGrantError("granted agent is not active")
                if not owner_active:
                    # An owned agent follows its owner's account status (a
                    # soft-deleted user has ``active`` false).
                    log_agent_owner_inactive(agent.id, agent.owner_id)
                    raise InvalidGrantError("granted agent is not active")
                grant_id = grant.id
                grant_agent_id = grant.agent_id
                grant_scopes = list(grant.scopes)
                # Reported set (RFC 6749 §5.1) — the resolvers ignore the
                # snapshot for agent tokens and enforce live grants ∩ client
                # ceiling ∩ grant scopes from the token's first use, so the
                # consent-time D2 set alone would over-report a scope revoked
                # inside the code TTL (the consent-time snapshot is not
                # trusted across the TTL — same posture as the gates above).
                grant_effective_scopes = await resolve_effective_scopes(
                    session,
                    actor_id=grant.agent_id,
                    actor_type=ActorType.AGENT,
                    snapshot_scopes=grant_scopes,
                    is_ephemeral=False,
                    client_ceiling=(
                        frozenset(oauth_client.allowed_scopes)
                        if oauth_client.allowed_scopes is not None
                        else None
                    ),
                    grant_ceiling=frozenset(grant.scopes),
                )

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

        if grant_id is not None and grant_agent_id is not None:
            # Actor = the grant's AGENT, scopes = the consent-time grant set
            # (already the D2 triple intersection, OIDC-stripped). Both
            # lineage columns are stamped so the kill switches reach these
            # rows. No id_token (D11): the client asked to wire up an agent,
            # not to learn who the consenting human is.
            #
            # Known benign race: the grant was checked in the exchange
            # transaction above, but issue_pair mints in its own transaction —
            # a :revoke landing between the two can leave freshly-minted rows
            # that miss the revoke sweep. They never resolve (every resolver
            # re-checks grant status live) and simply linger until expiry.
            access_token, refresh_token = await self._token_svc.issue_pair(
                grant_agent_id,
                ActorType.AGENT,
                grant_scopes,
                oauth_client_id=oauth_client_id,
                oauth_grant_id=grant_id,
            )
            return access_token, refresh_token, None, grant_effective_scopes

        scopes = auth_code.scopes.split() if auth_code.scopes else ["openid"]
        access_token, refresh_token = await self._token_svc.issue_pair(
            user.id, ActorType.USER, scopes, oauth_client_id=oauth_client_id
        )

        id_token = issue_id_token(
            self._auth_config,
            issuer=issuer or resolved_auth_base_url(self._ctx.config),
            sub=user.id,
            email=user.email,
            aud=client_id,
            nonce=auth_code.nonce,
        )

        # Reported (§5.1) as granted at authorize time, deliberately: the user
        # channel's set may include OIDC passthrough scopes (openid/email/…)
        # that are consumed by the id_token and never enforced as platform
        # permissions, and enforcement additionally intersects the *live*
        # client ceiling at resolve time. Standard OIDC posture — the scope
        # member tells the client what its authorization covers (including
        # the identity scopes), not the platform-permission subset.
        return access_token, refresh_token, id_token, scopes

    async def _resolve_or_create_user(self, claims: IdpClaims) -> str:
        """Resolve external identity to existing user or create a new one.

        Auto-links to an existing account by email only when the IdP asserts
        email_verified=true. When the email is unverified and already belongs to
        a local account, the login is rejected (fail closed) rather than linked
        or silently creating a duplicate — emails are unique, so a duplicate is
        impossible and a takeover via unverified email must not be allowed.

        Handles the race condition where concurrent callbacks for the same
        external_subject both pass the initial lookup — the UniqueConstraint
        on (issuer, external_subject) rejects the second insert, which is
        caught and retried as a lookup.
        """
        provider = self._auth_config.idp.provider
        issuer = self._effective_idp_issuer(claims)

        async with self._ctx.admin_db.transaction() as session:
            ext_id = await ExternalIdentityRepository.get_by_issuer_subject(
                session, issuer, claims.external_subject
            )
            if ext_id is not None:
                return ext_id.user_id
            # A link with no real issuer is pinned to the issuer that adopts it,
            # so no other issuer can resolve it afterwards.
            unscoped = await self._adoptable_unscoped_link(
                session, provider, issuer, claims.external_subject
            )
            if unscoped is not None:
                before_issuer = unscoped.issuer
                if await ExternalIdentityRepository.pin_issuer(session, unscoped, issuer):
                    await record_audit(
                        session,
                        action=AuditAction.UPDATE,
                        target_type=AuditTargetType.USER,
                        target_id=unscoped.user_id,
                        actor_type=ActorType.USER,
                        actor_id=unscoped.user_id,
                        before={"external_identity_issuer": before_issuer},
                        after={"external_identity_issuer": issuer},
                        reason=f"keyed external identity ({provider}) to its issuer",
                        origin=None,
                    )
                    return unscoped.user_id
                # A concurrent first login pinned it first: it resolves only if
                # that login pinned it to this issuer.
                ext_id = await ExternalIdentityRepository.get_by_issuer_subject(
                    session, issuer, claims.external_subject
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
                        issuer=issuer,
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
                    issuer=issuer,
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
            ext_id = await ExternalIdentityRepository.get_by_issuer_subject(
                session, issuer, claims.external_subject
            )
            if ext_id is not None:
                return ext_id.user_id
            raise InvalidGrantError("concurrent identity creation failed")

    @staticmethod
    async def _adoptable_unscoped_link(
        session: AsyncSession, provider: str, issuer: str, external_subject: str
    ) -> ExternalIdentity | None:
        """The link with no real issuer that ``issuer`` may resolve for ``external_subject``.

        Such a link (``issuer`` empty or equal to ``provider``) was written
        before links were keyed by issuer, under whichever issuer the provider
        was configured with then; the configured issuer adopts it on first
        sign-in. A link of the provider keyed to a different real issuer shows
        the provider has signed in through another issuer since links were
        keyed, so the unscoped link may belong to that issuer, and the same
        subject at another issuer can be a different person: it is not
        adopted, and its user can still sign in through a verified-email match.
        """
        link = await ExternalIdentityRepository.get_unscoped(session, provider, external_subject)
        if link is None:
            return None
        if await ExternalIdentityRepository.has_link_under_other_issuer(session, provider, issuer):
            _logger.warning(
                "external_identity_unscoped_link_not_adopted",
                provider=provider,
                issuer=issuer,
                external_identity_id=link.id,
            )
            return None
        return link

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
