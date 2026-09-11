/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_consentAgentCreate } from '../models/Body_consentAgentCreate';
import type { Body_consentSubmit } from '../models/Body_consentSubmit';
import type { Body_loginSubmit } from '../models/Body_loginSubmit';
import type { ConsentAgentStatusResponse } from '../models/ConsentAgentStatusResponse';
import type { IntrospectRequest } from '../models/IntrospectRequest';
import type { IntrospectResponse } from '../models/IntrospectResponse';
import type { MintRequest } from '../models/MintRequest';
import type { MintResponse } from '../models/MintResponse';
import type { OAuthApprovalDecisionRequest } from '../models/OAuthApprovalDecisionRequest';
import type { OAuthApprovalStatusResponse } from '../models/OAuthApprovalStatusResponse';
import type { OAuthGrantAdminListResponse } from '../models/OAuthGrantAdminListResponse';
import type { OAuthSessionContinueRequest } from '../models/OAuthSessionContinueRequest';
import type { OAuthSessionContinueResponse } from '../models/OAuthSessionContinueResponse';
import type { RevokeRequest } from '../models/RevokeRequest';
import type { TokenResponse } from '../models/TokenResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OAuthService {
    /**
     * List OAuth grants
     * List consent→agent grants across all clients and agents.
     *
     * The admin cross-view over the grant registry: filter by client, agent,
     * consenting user, or status. Each item carries the client's display name
     * and redirect-URI origin plus the consenting ``user_id`` — after an agent
     * ownership transfer the grant stays with the original consenter, so this
     * column is how an admin spots stranded grants.
     * @returns OAuthGrantAdminListResponse Successful Response
     * @throws ApiError
     */
    public static listOauthGrants({
        clientId,
        agentId,
        userId,
        status,
        limit = 50,
        cursor,
    }: {
        /**
         * Filter by the client's public client_id.
         */
        clientId?: (string | null),
        /**
         * Filter by bound agent.
         */
        agentId?: (string | null),
        /**
         * Filter by consenting user.
         */
        userId?: (string | null),
        /**
         * Filter by grant lifecycle state.
         */
        status?: ('active' | 'revoked' | null),
        limit?: number,
        cursor?: (string | null),
    }): CancelablePromise<OAuthGrantAdminListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/admin/oauth-grants',
            query: {
                'client_id': clientId,
                'agent_id': agentId,
                'user_id': userId,
                'status': status,
                'limit': limit,
                'cursor': cursor,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Authorize Endpoint
     * RFC 6749 Authorization endpoint with PKCE (S256 only).
     *
     * If an external IdP is configured, redirects to the upstream provider.
     * Otherwise returns an error (direct login requires a separate credential exchange).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static authorizeEndpoint({
        responseType,
        clientId,
        redirectUri,
        codeChallenge,
        codeChallengeMethod,
        scope = 'openid',
        state,
        nonce,
        sc,
    }: {
        responseType: string,
        clientId: string,
        redirectUri: string,
        codeChallenge: string,
        codeChallengeMethod: string,
        scope?: string,
        state?: (string | null),
        nonce?: (string | null),
        /**
         * Signed session-continuation blob minted by POST /oauth/session/continue (identity-ladder rung 1). Optional; an invalid or absent value leaves the flow byte-identical to before.
         */
        sc?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/authorize',
            query: {
                'response_type': responseType,
                'client_id': clientId,
                'redirect_uri': redirectUri,
                'code_challenge': codeChallenge,
                'code_challenge_method': codeChallengeMethod,
                'scope': scope,
                'state': state,
                'nonce': nonce,
                'sc': sc,
            },
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Error Page
     * Minimal error endpoint for browser-facing authorization failures.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static errorPage({
        error = 'unknown_error',
    }: {
        error?: string,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/error',
            query: {
                'error': error,
            },
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Local-account login form (authorization flow)
     * Render the local-account login form for an in-flight ``/authorize`` request.
     *
     * Verifies the ``ls`` signature/TTL/purpose **before** rendering — an
     * expired, forged, or wrong-purpose token never gets a form — re-checks the
     * D7 client gate (a client denied or deactivated while the user holds the
     * ``ls`` must not be asked for a password), rejects an already-spent ``ls``,
     * and embeds ``ls`` plus a fresh single-use CSRF nonce bound to it.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static loginPage({
        ls,
    }: {
        /**
         * Signed authorization-flow state (carry-through token)
         */
        ls: string,
    }): CancelablePromise<string> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/login',
            query: {
                'ls': ls,
            },
            errors: {
                400: `Bad Request`,
                404: `Local-account login is unavailable (\`auth.local_login.enabled=false\`, or an external IdP is configured — \`auth.idp.enabled=true\` — which always wins): the route answers the framework's plain route-not-found 404, so the gate state is unobservable.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Local-account login submit (authorization flow)
     * Authenticate the local account and rejoin the authorization flow.
     *
     * Success never mints a JWT — it flows straight into code issuance (platform
     * client) or the consent handle (registered third-party client), exactly
     * where the IdP callback rejoins, and burns the single-use ``ls``.
     * Credential failures re-render the form with one generic message: lockout
     * state, unknown email, and wrong password are indistinguishable (no
     * user-enumeration response oracle), while the shared
     * ``AuthService.authenticate`` core still increments the failed-login count
     * and applies the lockout threshold. An account flagged
     * ``must_change_password`` authenticates but is told to rotate via the UI
     * first — the OAuth plane must not hand a fully-scoped token to a
     * temporary-password principal the UI would have boxed into
     * change-password-only.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static loginSubmit({
        formData,
    }: {
        formData: Body_loginSubmit,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/login',
            formData: formData,
            mediaType: 'application/x-www-form-urlencoded',
            errors: {
                400: `Bad Request`,
                404: `Local-account login is unavailable (\`auth.local_login.enabled=false\`, or an external IdP is configured — \`auth.idp.enabled=true\` — which always wins): the route answers the framework's plain route-not-found 404, so the gate state is unobservable.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Revoke OAuth grant
     * Revoke a consent→agent grant — one of the three revocation kill radii.
     *
     * Allowed for the grant's owner (the consenting user) or an admin. Marks
     * the grant ``revoked`` and revokes every outstanding access/refresh token
     * minted under it in the same transaction; the live resolvers also re-check
     * grant status on every verdict (belt + braces). The client's next token
     * use or refresh fails closed. Idempotent on an already-revoked grant.
     * @returns void
     * @throws ApiError
     */
    public static revokeOauthGrant({
        grantId,
    }: {
        grantId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth-grants/{grant_id}:revoke',
            path: {
                'grant_id': grantId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Approve or deny a pending client inline (approval-pending page)
     * Thin wrapper over the admin approval path for the approval-pending page.
     *
     * Authorization is byte-identical to ``POST /admin/oauth-clients/{id}:approve``
     * / ``:deny`` (``oauth-clients:write``, org:admin implies it) and the
     * decision itself is the SAME ``OAuthClientService.approve``/``deny`` calls —
     * same audit records, same D7 active/approval_status coupling; this endpoint
     * only translates the signed state blob into the client row. CSRF posture
     * matches the consent POST: no ambient credential is honored — the browser
     * must explicitly present the SPA bearer token, which a cross-site form
     * cannot do.
     * @returns OAuthApprovalStatusResponse Successful Response
     * @throws ApiError
     */
    public static approvalDecisionEndpoint({
        requestBody,
    }: {
        requestBody: OAuthApprovalDecisionRequest,
    }): CancelablePromise<OAuthApprovalStatusResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/approval/decision',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Malformed, tampered, or expired approval-state blob.`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Poll client approval status (approval-pending page)
     * Minimal tri-state poll for the approval-pending page.
     *
     * Anonymous but keyed by the signed approval-state blob — never a bare
     * client_id, so the endpoint cannot be used to enumerate registrations. Any
     * verification failure (bad signature, wrong purpose, expired ``iat``,
     * malformed blob) is a 400 ``invalid_grant``; the page reacts to a 400 by
     * re-running /authorize, which mints a fresh blob. The response carries ONLY
     * the tri-state — no names, redirect URIs, or metadata.
     * @returns OAuthApprovalStatusResponse Successful Response
     * @throws ApiError
     */
    public static approvalStatusEndpoint({
        st,
    }: {
        /**
         * Signed approval-state blob minted by /authorize
         */
        st: string,
    }): CancelablePromise<OAuthApprovalStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth/approval/status',
            query: {
                'st': st,
            },
            errors: {
                400: `Malformed, tampered, or expired approval-state blob.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Authorize Oauth Callback
     * External IdP callback — exchanges upstream code and issues platform auth code.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static authorizeOauthCallback({
        code,
        state,
    }: {
        code: string,
        state: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth/callback',
            query: {
                'code': code,
                'state': state,
            },
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Consent Page
     * Display the OAuth consent screen.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static consentPage({
        ch,
    }: {
        /**
         * Opaque consent-flow handle
         */
        ch: string,
    }): CancelablePromise<string> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth/consent',
            query: {
                'ch': ch,
            },
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Consent Submit
     * Process the consent form submission. Mints the auth code only on approval.
     *
     * ``consent_token`` is the opaque handle emitted by the callback. It never
     * leaves the state backend as anything more than an ID — the actual consent
     * parameters (user_id, email, scopes, redirect_uri) live server-side and
     * can't be tampered with or captured from browser history/proxy logs.
     *
     * ``agent_id`` is posted only by the agent-picker variant
     * (``consent_model='agent'`` clients); it is validated and the scope math
     * recomputed entirely server-side — the browser's selection is never
     * trusted.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static consentSubmit({
        formData,
    }: {
        formData: Body_consentSubmit,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/consent',
            formData: formData,
            mediaType: 'application/x-www-form-urlencoded',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Create the consenting user's first agent inline (consent page)
     * Create the consenting user's first agent from the zero-agents consent page (P4).
     *
     * The form is rendered only when the consenting user owns zero agents in
     * any status (the G12(b) first-run dead-end). This submit verifies the signed
     * single-use ``agent-create`` blob (bound to the consent handle AND the
     * authenticated subject — no ambient credential is honored, so a cross-site
     * form cannot drive it: it would need both the unguessable handle and a
     * blob minted for that very handle), re-validates the handle and the D7
     * client gate, provisions the user row if deferred provisioning left none
     * (an affirmative user action, unlike rendering), re-checks the zero-agents
     * predicate (an agent appearing in between skips creation — idempotent),
     * creates the agent through the same ``AgentService.create`` path as the
     * SPA (owner = the consenting user, default agent scopes, same audit +
     * event), and 303-redirects back into ``GET /oauth/consent`` where the new
     * agent renders pre-selected.
     *
     * Failure arms: expired/tampered/replayed blob and expired handle → the
     * consent flow's standard ``invalid_consent`` error redirect; a gated
     * client → ``access_denied``; an invalid agent name → the form re-rendered
     * with the error inline and a fresh blob.
     *
     * Creation posture (security review — the hybrid): the arm is decided
     * server-side AFTER the subject is resolved/provisioned, against the same
     * effective-permission math as POST /agents' ``agents:write`` gate. A
     * permissioned user gets the original behaviour (ACTIVE + 303 re-entry); an
     * unpermissioned one gets a PENDING agent (the POST /register posture) and
     * the awaiting-approval page. A per-subject ``set_if_absent`` slot claim
     * makes N parallel submits (N distinct blobs from N renders) create exactly
     * one agent — losers re-enter consent, which renders the picker or the
     * awaiting page as appropriate.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static consentAgentCreate({
        formData,
    }: {
        formData: Body_consentAgentCreate,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/consent/agent',
            formData: formData,
            mediaType: 'application/x-www-form-urlencoded',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Poll pending-agent approval status (consent awaiting page)
     * Minimal tri-state poll for the pending-agent awaiting page (P4 hybrid).
     *
     * Anonymous but keyed by the signed ``agent-status`` blob — never a bare
     * agent id, so the endpoint cannot be used to enumerate or probe agents:
     * the id it reports on is the one SIGNED into the blob, which only the
     * consent flow mints, and only for an agent it verified belongs to the
     * handle's subject. The response carries ONLY the tri-state — no name,
     * owner, or scopes — and non-terminal lifecycle states (disabled, archived,
     * a vanished row) all read as ``pending``, so possession of a blob is not a
     * lifecycle oracle either. Any verification failure (bad signature, wrong
     * purpose, expired ``iat``, malformed blob) is a 400 ``invalid_grant``; the
     * page treats a 400 as terminal ("retry the connection") because the blob
     * shares the consent handle's lifetime — a retry re-enters the flow, which
     * re-parks on a fresh awaiting page while the agent stays pending. Shares
     * the approval-status poll's own per-IP rate bucket (same cadence, same
     * caller shape).
     * @returns ConsentAgentStatusResponse Successful Response
     * @throws ApiError
     */
    public static consentAgentStatus({
        st,
    }: {
        /**
         * Signed agent-status blob minted by the consent flow's pending arm
         */
        st: string,
    }): CancelablePromise<ConsentAgentStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth/consent/agent/status',
            query: {
                'st': st,
            },
            errors: {
                400: `Malformed, tampered, or expired agent-status blob.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Introspect Endpoint
     * Introspect a token (RFC 7662).
     * @returns IntrospectResponse Successful Response
     * @throws ApiError
     */
    public static introspectEndpoint({
        requestBody,
    }: {
        requestBody: IntrospectRequest,
    }): CancelablePromise<IntrospectResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/introspect',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Mint Endpoint
     * Mint a short-lived ephemeral token for a task agent.
     *
     * The caller must be an authenticated service account. The requested scopes
     * must be a subset of the caller's own scopes.
     * @returns MintResponse Successful Response
     * @throws ApiError
     */
    public static mintEndpoint({
        requestBody,
    }: {
        requestBody: MintRequest,
    }): CancelablePromise<MintResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/mint',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Revoke Endpoint
     * Revoke a token (RFC 7009). Always returns 200 for valid requests.
     *
     * Two client-authentication arms, negotiated on the request content type by
     * ``_RevocationRoute`` (this function body IS the JSON arm — form-encoded
     * requests never reach it):
     *
     * - **Form-encoded** (`application/x-www-form-urlencoded`, RFC 7009 §2.1 —
     * the shape MCP OAuth clients send, G11): `token` + optional
     * `token_type_hint` + `client_id`. Public (secret-less) clients
     * authenticate by client_id **lineage binding** — the call revokes
     * anything only when the token exists and was issued to that `client_id`;
     * everything else is a 200 no-op (no token-validity oracle). Revoking an
     * access token kills that token only; revoking a **refresh token is a full
     * disconnect** — every token of the consent grant AND the grant row itself
     * die, so reconnecting requires fresh consent (deliberately beyond the
     * RFC 7009 §2.1 SHOULD; one revocation semantics platform-wide). This arm
     * is gated by `server.mcp.oauth.enabled` (plain 404 when off), capped at
     * 64 KiB declared body length, and per-IP rate limited; its errors speak
     * the RFC 6749 §5.2 dialect (RFC 7009 §2.2.1), not Problem Details.
     * - **JSON** (any other content type — the pre-G11 contract, byte-identical
     * including 422 shapes): requires a platform bearer identity; revokes the
     * caller's own token (access → that token, refresh → its family). Used by
     * `jentic logout`.
     *
     * Revocation residual: a revoked **access** token dies on the control-plane
     * resolver immediately, but the broker's `CachedTokenValidator` (30 s TTL)
     * may honour an already-cached verdict for up to 30 s — the same residual as
     * the UI `:revoke` kill switch and the G10 transfer sweep.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static revokeEndpoint({
        requestBody,
    }: {
        requestBody: RevokeRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/revoke',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Form-encoded (RFC 7009) requests only: missing \`token\` (RFC 6749 §5.2 dialect per RFC 7009 §2.2.1): \`{"error": "invalid_request", "error_description": "..."}\`.`,
                401: `Unauthorized`,
                404: `Form-encoded (RFC 7009) requests only: interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`), so the RFC 7009 arm answers the framework's plain route-not-found 404 (gate state unobservable — same posture as the anonymous DCR door). The bearer-authenticated JSON arm is not gated.`,
                413: `Form-encoded (RFC 7009) requests only: declared Content-Length exceeds the 64 KiB raw-body cap (RFC 6749 §5.2 dialect).`,
                422: `Unprocessable Entity`,
                429: `Form-encoded (RFC 7009) requests only: per-IP rate limit exceeded (\`Retry-After\` header set; RFC 6749 §5.2 dialect body, \`error=slow_down\` per RFC 8628 §3.5).`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Exchange a live platform session for an authorize continuation
     * Rung 1 of the /authorize identity ladder: reuse the platform session.
     *
     * The login page's script posts the pending authorize state (the ``ls``
     * carry-through token) with the SPA's bearer token in the Authorization
     * header — no cookies, no ambient credentials, so a cross-site form cannot
     * drive it (same CSRF posture as the consent POST and the inline approval
     * decision). The platform token is validated by the standard auth
     * dependency (users only), and the ``active`` / ``must_change_password``
     * fences are re-checked with a LIVE user-row read — not the token's baked
     * claims — matching rung 3's ``password_rotation_required`` posture, so an
     * admin-forced reset fences the exchange immediately even while pre-reset
     * SPA tokens are still in flight. The D7 client gate is re-checked, and on
     * success the response carries a relative ``/authorize`` resume URL bearing
     * a short-TTL, ``session``-purpose continuation blob that pins THIS
     * caller's ``user_id`` — the identity is fixed at exchange time, before the
     * consent page renders it with its "Not you?" escape.
     *
     * Every failure after authentication is the same generic 400: an invalid
     * blob must not let the caller learn anything about the client or the flow.
     * @returns OAuthSessionContinueResponse Successful Response
     * @throws ApiError
     */
    public static sessionContinueEndpoint({
        requestBody,
    }: {
        requestBody: OAuthSessionContinueRequest,
    }): CancelablePromise<OAuthSessionContinueResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/session/continue',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Malformed, tampered, expired, or otherwise unusable authorize state — one generic rejection, never a reason.`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Local-account login is unavailable (\`auth.local_login.enabled=false\`, or an external IdP is configured — \`auth.idp.enabled=true\` — which always wins): the route answers the framework's plain route-not-found 404, so the gate state is unobservable.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Token Endpoint
     * Exchange a refresh token, JWT assertion, authorization code, or client creds for tokens.
     *
     * Error responses speak the RFC 6749 §5.2 dialect (top-level ``error`` +
     * ``error_description``), NOT platform Problem Details — reshaped by
     * ``_TokenRoute``. Malformed/missing parameters answer ``invalid_request``;
     * failed client authentication answers ``invalid_client`` (status 401 with a
     * ``WWW-Authenticate: Basic`` challenge when the client attempted HTTP Basic,
     * 400 otherwise). On the refresh arm, a revoked consent grant answers
     * ``invalid_grant`` with ``error_description: "consent grant has been
     * revoked"`` — terminal; restart the authorization flow.
     * @returns TokenResponse Successful Response
     * @throws ApiError
     */
    public static tokenEndpoint({
        requestBody,
    }: {
        requestBody: {
            assertion?: (string | null);
            client_id?: (string | null);
            client_secret?: (string | null);
            code?: (string | null);
            code_verifier?: (string | null);
            grant_type: string;
            redirect_uri?: (string | null);
            refresh_token?: (string | null);
        },
    }): CancelablePromise<TokenResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/token',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `RFC 6749 §5.2 error dialect (NOT platform Problem Details — this is a spec-facing endpoint real OAuth/MCP clients parse): \`{"error": "invalid_request" | "invalid_grant" | "invalid_client" | "unsupported_grant_type", "error_description": "..."}\`. \`invalid_request\` covers malformed/missing parameters; a revoked consent grant surfaces on the refresh arm as \`invalid_grant\` with \`error_description: "consent grant has been revoked"\` — clients should treat it as terminal and restart the authorization flow. A client whose authentication fails after attempting HTTP Basic (\`Authorization\` header, RFC 6749 §2.3.1) gets the same \`invalid_client\` dialect body with status 401 and a \`WWW-Authenticate: Basic\` challenge, per §5.2.`,
                422: `Unprocessable Entity`,
                429: `Per-client+IP rate limit exceeded (\`Retry-After\` header set; RFC 6749 §5.2 dialect body, \`error=slow_down\` per RFC 8628 §3.5).`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
