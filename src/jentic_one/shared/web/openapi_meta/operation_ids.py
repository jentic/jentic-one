"""Per-operation identity: operationIds, tags, and the security-adjusting sets.

:func:`generate_operation_id` names every operation and :func:`resolve_tag`
files it under one tag from :mod:`.tags`; the operationId sets below single
out the operations whose ``security`` or ``422`` response differs from the
authenticated default.
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute

# Operations that are intentionally unauthenticated and must NOT inherit the
# global BearerAuth requirement.
#
# Every entry carries its own ``#`` justification comment on the line(s)
# directly above it — what it returns and what binds or limits it in place of a
# platform token. ``tests/arch/test_route_auth_gate.py`` enforces this, fails
# on entries that match no real route, and fails on any route that neither
# authenticates nor is declared here.
PUBLIC_OPERATION_IDS: frozenset[str] = frozenset(
    {
        # Root liveness probe of the combined app: dependency-free, returns only
        # a status string and the running version.
        "getHealth",
        # Backend-identity probe: unauthenticated, self-describing, no secrets.
        "getInstance",
        # Admin-surface health probe (``/admin/health`` combined, ``/health``
        # standalone): liveness plus the setup_required first-run flag the SPA
        # and CLI read before any user exists. No tenant data.
        "health",
        # Control-surface liveness probe: dependency-free, returns only the
        # surface name and status.
        "controlHealth",
        # Registry-surface liveness probe: dependency-free, returns only the
        # surface name and status.
        "registryHealth",
        # Auth-surface liveness probe: dependency-free, returns only the
        # surface name and status.
        "authHealth",
        # Password login: the caller has no token yet; the credential is the
        # request body, verified by AuthService.authenticate.
        "login",
        # Invite redemption: the caller has no token yet; bound by the
        # single-use invite token in the request body.
        "redeemInvite",
        # First-run setup: create the first admin. Self-closes (410) once any
        # user exists, so it is safe to leave unauthenticated.
        "createAdmin",
        # OAuth token endpoint: the caller has no platform token yet (it calls
        # this to *get* one); authenticated by the grant itself (code + PKCE,
        # refresh token, client assertion). Rate limited.
        "tokenEndpoint",
        # OAuth /authorize entry point: a browser starting the flow has no
        # platform token yet; bound by the registered client and its redirect
        # URI, and carries the flow forward in signed state. Rate limited.
        "authorizeEndpoint",
        # Agent dynamic registration (RFC 7591): the agent has no platform
        # token yet; it receives a pending registration that an admin must
        # approve before any token is issued.
        "registerEndpoint",
        # Anonymous OAuth-client DCR front door: flagship MCP
        # clients register anonymously; the boundary is admin approval +
        # consent, not registration. Rate limited and config-gated instead.
        "registerOauthClientEndpoint",
        # Credential-connect OAuth redirect callback: the upstream provider
        # redirects the browser here; bound by a signed state param, not a session.
        "oauthCallback",
        # IdP login redirect callback on the /authorize flow: bound by the
        # signed authorize state param, not a session. Rate limited.
        "authorizeOauthCallback",
        # Approval-pending status poll: anonymous by design (the polling
        # browser has no platform token yet), bound by the signed
        # approval-state blob minted at /authorize — not a session and not a
        # bare client_id. Returns only the pending/approved/denied tri-state;
        # rate limited in its own bucket instead.
        "approvalStatusEndpoint",
        # OAuth consent screen (presented after IdP login, before issuing the
        # code): bound by the consent handle from the signed flow state.
        "consentPage",
        # OAuth consent form submission: same browser-mid-flow caller as the
        # consent page; bound by the consent handle and rate limited. Mints the
        # auth code only on approval.
        "consentSubmit",
        # Inline first-agent creation on the zero-agents consent page (P4):
        # same browser-mid-flow caller as the consent submit (no platform
        # token yet); bound by the consent handle + a signed single-use
        # agent-create blob, rate limited like the other consent endpoints.
        "consentAgentCreate",
        # Pending-agent status poll (P4 hybrid awaiting page): anonymous by
        # design like approvalStatusEndpoint — the polling browser has no
        # platform token — and keyed by the signed agent-status blob the
        # consent flow minted for one specific agent, never a bare agent id.
        # Returns only the pending/approved/denied tri-state; rate limited in
        # the approval-status bucket instead.
        "consentAgentStatus",
        # Local-account login form on the /authorize flow: the caller is a
        # browser mid-authorization with no token yet. Config-gated
        # (auth.local_login.enabled → 404) and rate limited instead.
        "loginPage",
        # Local-account login form submission: the credential is the request
        # body; bound by the signed authorize state and a single-use CSRF
        # nonce. Config-gated (404) and rate limited like the login page.
        "loginSubmit",
        # Browser-facing OAuth error page (no auth; just renders an error code).
        "errorPage",
        # JWKS: the public signing keys clients need to verify tokens; public
        # key material only, unauthenticated by spec.
        "jwks",
        # RFC 8414 authorization-server metadata: endpoint URLs and supported
        # algorithms only, unauthenticated by spec.
        "oauthAuthorizationServer",
        # /mcp-scoped RFC 8414 discovery document for the path-scoped issuer.
        # Unauthenticated by spec; config-gated (404) instead.
        "mcpOauthAuthorizationServer",
        # RFC 9728 protected-resource metadata for /mcp. Unauthenticated by
        # spec; config-gated (404) instead.
        "mcpOauthProtectedResource",
        # Root-path alias of the RFC 9728 /mcp protected-resource document.
        # Unauthenticated by spec; config-gated (404) instead.
        "mcpOauthProtectedResourceRootAlias",
        # Public IdP-login capability hint (enabled flag + provider name only;
        # no secrets). The SPA reads this pre-login to render the SSO button.
        "authIdpDescriptor",
    }
)


#: Operations that **do** authenticate, but not via the global ``BearerAuth``
#: platform token, so they must not advertise that security requirement. Unlike
#: :data:`PUBLIC_OPERATION_IDS` these are *not* public: they still reject bad
#: credentials with ``401``, so the documented error responses are kept intact.
#: Their ``security`` is dropped to ``[]`` (no platform bearer requirement); the
#: real credential is described in the endpoint reference's ``auth_note`` (see
#: ``NON_IDENTITY_AUTH`` in ``endpoint_permissions.py``).
NON_BEARER_AUTH_OPERATION_IDS: frozenset[str] = frozenset(
    {
        # RFC 7592 registration-status poll: authenticated by the
        # Registration-Access-Token issued at registration, not a platform bearer
        # token. It still returns 401 on a missing/invalid/expired RAT
        # (RegistrationAccessDeniedError -> 401), so the 401 response stays.
        "pollStatusEndpoint",
        # RFC 7009 token revocation (G11): dual-arm client authentication. The
        # form-encoded arm authenticates by OAuth client_id lineage binding
        # (public clients, auth method "none" — no platform bearer); the JSON
        # arm keeps the pre-G11 platform-bearer contract and still 401s on a
        # bad bearer, so the 401 response stays while the blanket BearerAuth
        # requirement is dropped.
        "revokeEndpoint",
    }
)


#: Operations whose request-validation failures are reshaped at the router into
#: their governing spec's error dialect, so the FastAPI 422 can never be
#: returned and the auto-generated 422 response is dropped from the spec:
#: the DCR door's RFC 7591 §3.2.2 ``400 {"error": "invalid_client_metadata"}``
#: (``_Rfc7591Route`` in ``auth/web/routers/oauth_client_registration.py``) and
#: the token endpoint's RFC 6749 §5.2 dialect (``_TokenRoute`` in
#: ``auth/web/routers/oauth.py`` — body parsing runs entirely inside
#: ``_parse_token_request``, which answers §5.2 ``invalid_request``). The
#: route-specific error responses are documented on the route decorators.
ROUTER_RESHAPED_422_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "registerOauthClientEndpoint",
        "tokenEndpoint",
    }
)


#: Operations that exist only to refuse: the RFC 7592 client-update and
#: client-delete doors answer every call with ``OperationNotSupportedError``
#: (403), so they have no success body and FastAPI's inferred ``200`` describes a
#: response no caller can observe. The success entry is dropped from the spec so
#: a reader sees only what the endpoint can actually return.
ALWAYS_ERROR_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "deleteRegistrationEndpoint",
        "updateRegistrationEndpoint",
    }
)


def _camelize(name: str) -> str:
    """Convert a snake_case route function name to camelCase."""
    parts = [p for p in name.split("_") if p]
    if not parts:
        return name
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def generate_operation_id(route: APIRoute) -> str:
    """Derive an operationId from the handler name (camelCase).

    Used as FastAPI's ``generate_unique_id_function`` so every operation gets a
    stable, reference-style camelCase id (``create_credential`` -> "createCredential")
    instead of FastAPI's default ``create_credential_credentials_post``. Routes
    that pass an explicit ``operation_id=`` bypass this entirely, which is how
    collision-prone names (e.g. the per-surface ``health`` handlers) stay unique.
    """
    return _camelize(route.name)


# Ordered (regex, tag) rules mapping a request path to its fine-grained tag.
# First match wins, so more specific patterns precede broader ones. This is the
# single source of operation tagging: it overwrites whatever coarse tags the
# routers were registered with, reconciling everything to the reference names.
_TAG_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/health$"), "System"),
    (re.compile(r"^/[^/]+/health$"), "System"),
    (re.compile(r"^/instance$"), "System"),
    (re.compile(r"^/system/version$"), "System"),
    (re.compile(r"^/admin/config"), "Configuration"),
    (re.compile(r"^/credentials"), "Credentials"),
    (re.compile(r"^/permission-rule-sets"), "Permission Rule Sets"),
    (re.compile(r"^/integrations"), "Integrations"),
    (re.compile(r"^/connect-sessions"), "Integrations"),
    (re.compile(r"^/vendors"), "Vendors"),
    (re.compile(r"^/apis/.+/overlays"), "Overlays"),
    (re.compile(r"^/apis/.+/operations$"), "API Operations"),
    (re.compile(r"^/apis/.+/openapi$"), "API Spec"),
    (re.compile(r"^/apis"), "APIs"),
    # Identity-scoped host digest for integrators (#1278).
    (re.compile(r"^/governed-hosts$"), "Governed Hosts"),
    # Catalog (Discover) — browse/preview/import + the :refresh verb. Broad
    # ^/catalog is safe: no other surface shares the prefix.
    (re.compile(r"^/catalog"), "Catalog"),
    (re.compile(r"^/search"), "Search"),
    (re.compile(r"^/inspect"), "Inspect"),
    (re.compile(r"^/lookup"), "Lookup"),
    (re.compile(r"^/notes"), "Notes"),
    (re.compile(r"^/monitoring"), "Monitoring"),
    (re.compile(r"^/executions/holds"), "Held Executions"),
    (re.compile(r"^/executions"), "Executions"),
    (re.compile(r"^/jobs"), "Jobs"),
    (re.compile(r"^/events"), "Events"),
    (re.compile(r"^/mcp"), "MCP"),
    (re.compile(r"^/permissions"), "Permissions"),
    (re.compile(r"^/actors"), "Actors"),
    (re.compile(r"^/users"), "Users"),
    (re.compile(r"^/auth/(login|refresh)"), "Users"),
    (re.compile(r"^/auth/idp"), "Discovery"),
    (re.compile(r"^/audit"), "Audit"),
    (re.compile(r"^/admin/oauth-clients"), "OAuth Clients"),
    # Grant listings/revoke share the OAuth tag with the grant kill switch.
    (re.compile(r"^/admin/oauth-grants"), "OAuth"),
    # Anonymous DCR front door — before the broader ^/oauth rule below.
    (re.compile(r"^/oauth-clients"), "OAuth Clients"),
    # Admin-managed shared OAuth application registrations — before ^/oauth.
    (re.compile(r"^/oauth-app-registrations"), "OAuth App Registrations"),
    # Platform-actor surfaces (superset, not in the original reference).
    (re.compile(r"^/agents"), "Agents"),
    (re.compile(r"^/oauth"), "OAuth"),
    (re.compile(r"^/authorize"), "OAuth"),
    (re.compile(r"^/error"), "OAuth"),
    (re.compile(r"^/login$"), "OAuth"),
    (re.compile(r"^/register"), "Agent Registration"),
    (re.compile(r"^/\.well-known"), "Discovery"),
    (re.compile(r"^/me$"), "Identity"),
]


def resolve_tag(path: str) -> str | None:
    """Return the fine-grained tag for a request path, or ``None`` if unmatched."""
    for pattern, tag in _TAG_RULES:
        if pattern.match(path):
            return tag
    return None
