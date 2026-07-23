/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_introspectEndpoint } from '../models/Body_introspectEndpoint';
import type { Body_revokeEndpoint } from '../models/Body_revokeEndpoint';
import type { Body_tokenEndpoint } from '../models/Body_tokenEndpoint';
import type { IntrospectResponse } from '../models/IntrospectResponse';
import type { MintRequest } from '../models/MintRequest';
import type { MintResponse } from '../models/MintResponse';
import type { TokenResponse } from '../models/TokenResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OAuthService {
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
     * Oauth Callback
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
     * Introspect Endpoint
     * Introspect a token (RFC 7662).
     * @returns IntrospectResponse Successful Response
     * @throws ApiError
     */
    public static introspectEndpoint({
        formData,
    }: {
        formData: Body_introspectEndpoint,
    }): CancelablePromise<IntrospectResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/introspect',
            formData: formData,
            mediaType: 'application/x-www-form-urlencoded',
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
        formData,
    }: {
        formData: Body_revokeEndpoint,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/revoke',
            formData: formData,
            mediaType: 'application/x-www-form-urlencoded',
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
        formData,
    }: {
        formData: Body_tokenEndpoint,
    }): CancelablePromise<TokenResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth/token',
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
}
