/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthClientCreateRequest } from '../models/OAuthClientCreateRequest';
import type { OAuthClientCreateResponse } from '../models/OAuthClientCreateResponse';
import type { OAuthClientDenyRequest } from '../models/OAuthClientDenyRequest';
import type { OAuthClientListResponse } from '../models/OAuthClientListResponse';
import type { OAuthClientResponse } from '../models/OAuthClientResponse';
import type { OAuthClientRotateSecretResponse } from '../models/OAuthClientRotateSecretResponse';
import type { OAuthClientUpdateRequest } from '../models/OAuthClientUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OAuthClientsService {
    /**
     * List OAuth clients
     * List all registered OAuth clients, optionally filtered by approval status.
     * @returns OAuthClientListResponse Successful Response
     * @throws ApiError
     */
    public static listOauthClients({
        includeInactive = false,
        approvalStatus,
    }: {
        includeInactive?: boolean,
        /**
         * Filter by approval lifecycle state: pending, approved, or denied.
         */
        approvalStatus?: (string | null),
    }): CancelablePromise<OAuthClientListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/admin/oauth-clients',
            query: {
                'include_inactive': includeInactive,
                'approval_status': approvalStatus,
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
     * Register OAuth client
     * Register a new OAuth client for third-party application integration.
     *
     * The generated ``client_id`` and ``client_secret`` are returned in the
     * response. The secret is shown only once — store it securely.
     * @returns OAuthClientCreateResponse Successful Response
     * @throws ApiError
     */
    public static createOauthClient({
        requestBody,
    }: {
        requestBody: OAuthClientCreateRequest,
    }): CancelablePromise<OAuthClientCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/admin/oauth-clients',
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
     * Deactivate OAuth client
     * Soft-delete an OAuth client by setting active=False.
     *
     * Deactivated clients can no longer initiate authorization flows.
     * @returns void
     * @throws ApiError
     */
    public static deactivateOauthClient({
        id,
    }: {
        id: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/admin/oauth-clients/{id}',
            path: {
                'id': id,
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
     * Get OAuth client
     * Get an OAuth client by its internal ID.
     * @returns OAuthClientResponse Successful Response
     * @throws ApiError
     */
    public static getOauthClient({
        id,
    }: {
        id: string,
    }): CancelablePromise<OAuthClientResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/admin/oauth-clients/{id}',
            path: {
                'id': id,
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
     * Update OAuth client
     * Update an OAuth client's name, description, redirect_uris, or active status.
     * @returns OAuthClientResponse Successful Response
     * @throws ApiError
     */
    public static updateOauthClient({
        id,
        requestBody,
    }: {
        id: string,
        requestBody: OAuthClientUpdateRequest,
    }): CancelablePromise<OAuthClientResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/admin/oauth-clients/{id}',
            path: {
                'id': id,
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
     * Rotate client secret
     * Generate a new client secret. The previous secret is immediately invalidated.
     *
     * The new secret is shown only once — store it securely.
     * @returns OAuthClientRotateSecretResponse Successful Response
     * @throws ApiError
     */
    public static rotateOauthClientSecret({
        id,
    }: {
        id: string,
    }): CancelablePromise<OAuthClientRotateSecretResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/admin/oauth-clients/{id}/rotate-secret',
            path: {
                'id': id,
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
     * Approve OAuth client
     * Approve an OAuth client — sets approval_status=approved and active=true.
     *
     * Also re-approves a previously denied client (deny is reversible; rows are
     * never deleted, so the client's cached client_id becomes valid again).
     * @returns OAuthClientResponse Successful Response
     * @throws ApiError
     */
    public static approveOauthClient({
        id,
    }: {
        id: string,
    }): CancelablePromise<OAuthClientResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/admin/oauth-clients/{id}:approve',
            path: {
                'id': id,
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
     * Deny OAuth client
     * Deny an OAuth client — sets approval_status=denied and active=false.
     *
     * The row is retained so a later approve can reverse the decision.
     * @returns OAuthClientResponse Successful Response
     * @throws ApiError
     */
    public static denyOauthClient({
        id,
        requestBody,
    }: {
        id: string,
        requestBody?: (OAuthClientDenyRequest | null),
    }): CancelablePromise<OAuthClientResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/admin/oauth-clients/{id}:deny',
            path: {
                'id': id,
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
}
