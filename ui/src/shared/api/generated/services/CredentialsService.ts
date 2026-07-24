/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyCreateRequest } from '../models/ApiKeyCreateRequest';
import type { ApiKeyUpdateRequest } from '../models/ApiKeyUpdateRequest';
import type { BasicAuthCreateRequest } from '../models/BasicAuthCreateRequest';
import type { BasicAuthUpdateRequest } from '../models/BasicAuthUpdateRequest';
import type { BearerTokenCreateRequest } from '../models/BearerTokenCreateRequest';
import type { BearerTokenUpdateRequest } from '../models/BearerTokenUpdateRequest';
import type { ConnectChallengeResponse } from '../models/ConnectChallengeResponse';
import type { ConnectRequestBody } from '../models/ConnectRequestBody';
import type { CredentialCreateResponse } from '../models/CredentialCreateResponse';
import type { CredentialListResponse } from '../models/CredentialListResponse';
import type { CredentialRedactedResponse } from '../models/CredentialRedactedResponse';
import type { NoAuthCreateRequest } from '../models/NoAuthCreateRequest';
import type { OAuth2CreateRequest } from '../models/OAuth2CreateRequest';
import type { OAuth2UpdateRequest } from '../models/OAuth2UpdateRequest';
import type { ProviderDiscoveryResponse } from '../models/ProviderDiscoveryResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CredentialsService {
    /**
     * List credentials
     * List credentials with cursor-based pagination.
     * @returns CredentialListResponse Successful Response
     * @throws ApiError
     */
    public static listCredentials({
        cursor,
        limit = 50,
        vendor,
    }: {
        cursor?: (string | null),
        limit?: number,
        vendor?: (string | null),
    }): CancelablePromise<CredentialListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials',
            query: {
                'cursor': cursor,
                'limit': limit,
                'vendor': vendor,
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
     * Create credential
     * Create a new credential. The secret is returned once and never readable again.
     * @returns CredentialCreateResponse Successful Response
     * @throws ApiError
     */
    public static createCredential({
        requestBody,
    }: {
        requestBody: (BearerTokenCreateRequest | ApiKeyCreateRequest | BasicAuthCreateRequest | OAuth2CreateRequest | NoAuthCreateRequest),
    }): CancelablePromise<CredentialCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials',
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
     * OAuth connect callback
     * Handle the OAuth callback from the IdP.
     *
     * This endpoint is intentionally unauthenticated — it receives redirects
     * from external IdPs where the user has no session cookie. Security
     * binding is provided by the signed, time-limited state JWT which ties
     * the callback to a specific credential and caller.
     *
     * Redirects the popup the SPA opened to a public SPA route
     * (``/app/oauth/connected``) that owns the user-facing "you can close this"
     * experience and self-closes. Two variants, distinguished only by a coarse
     * ``status`` query param:
     *
     * * Success: ``?status=ok``.
     * * Failure: ``?status=error`` — no protocol or provider detail is
     * exposed in the redirect URL.
     *
     * The parent SPA still learns the real outcome by polling
     * ``GET /credentials/{id}`` — never from this redirect. The actual cause
     * (missing state, connect failure, provider error, etc.) is recorded via
     * structured logging for operators.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static oauthCallback({
        code,
        state,
        error,
    }: {
        code?: (string | null),
        state?: (string | null),
        error?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/oauth/callback',
            query: {
                'code': code,
                'state': state,
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
     * List credential providers
     * Return discovery metadata for all configured credential providers.
     * @returns ProviderDiscoveryResponse Successful Response
     * @throws ApiError
     */
    public static listProviders(): CancelablePromise<ProviderDiscoveryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/providers',
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
     * Delete credential
     * Delete a credential.
     * @returns void
     * @throws ApiError
     */
    public static deleteCredential({
        credentialId,
    }: {
        credentialId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/credentials/{credential_id}',
            path: {
                'credential_id': credentialId,
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
     * Get credential
     * Get a single credential with redacted secrets.
     * @returns CredentialRedactedResponse Successful Response
     * @throws ApiError
     */
    public static getCredential({
        credentialId,
    }: {
        credentialId: string,
    }): CancelablePromise<CredentialRedactedResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/{credential_id}',
            path: {
                'credential_id': credentialId,
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
     * Update or rotate credential
     * Update or rotate a credential.
     * @returns CredentialRedactedResponse Successful Response
     * @throws ApiError
     */
    public static updateCredential({
        credentialId,
        requestBody,
    }: {
        credentialId: string,
        requestBody: (BearerTokenUpdateRequest | ApiKeyUpdateRequest | BasicAuthUpdateRequest | OAuth2UpdateRequest),
    }): CancelablePromise<CredentialRedactedResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/credentials/{credential_id}',
            path: {
                'credential_id': credentialId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Begin OAuth connect flow
     * Initiate the OAuth connect flow for a credential.
     * @returns ConnectChallengeResponse Successful Response
     * @throws ApiError
     */
    public static connectCredential({
        credentialId,
        requestBody,
    }: {
        credentialId: string,
        requestBody: ConnectRequestBody,
    }): CancelablePromise<ConnectChallengeResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials/{credential_id}/connect',
            path: {
                'credential_id': credentialId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Credential is not connectable`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
