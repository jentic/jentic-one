/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_consentSubmit } from '../models/Body_consentSubmit';
import type { IntrospectRequest } from '../models/IntrospectRequest';
import type { IntrospectResponse } from '../models/IntrospectResponse';
import type { MintRequest } from '../models/MintRequest';
import type { MintResponse } from '../models/MintResponse';
import type { OAuthGrantAdminListResponse } from '../models/OAuthGrantAdminListResponse';
import type { RevokeRequest } from '../models/RevokeRequest';
import type { TokenResponse } from '../models/TokenResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OAuthService {
    /**
     * List OAuth grants
     * List consent→agent grants across all clients and agents (§4.8).
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
    }: {
        responseType: string,
        clientId: string,
        redirectUri: string,
        codeChallenge: string,
        codeChallengeMethod: string,
        scope?: string,
        state?: (string | null),
        nonce?: (string | null),
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
     * Revoke OAuth grant
     * Revoke a consent→agent grant — one of the three §4.6 kill radii.
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
     * ``agent_id`` is posted only by the §4.4 agent-picker variant
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
     * Revoke a token (RFC 7009). Always returns 200.
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
     * Token Endpoint
     * Exchange a refresh token, JWT assertion, authorization code, or client creds for tokens.
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
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
