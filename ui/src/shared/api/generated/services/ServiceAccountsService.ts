/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyResponse } from '../models/ApiKeyResponse';
import type { jentic_one__auth__web__schemas__service_accounts__DenyRequest } from '../models/jentic_one__auth__web__schemas__service_accounts__DenyRequest';
import type { ServiceAccountCreateRequest } from '../models/ServiceAccountCreateRequest';
import type { ServiceAccountListResponse } from '../models/ServiceAccountListResponse';
import type { ServiceAccountResponse } from '../models/ServiceAccountResponse';
import type { ServiceAccountScopesRequest } from '../models/ServiceAccountScopesRequest';
import type { ServiceAccountScopesResponse } from '../models/ServiceAccountScopesResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ServiceAccountsService {
    /**
     * List Service Accounts
     * List service accounts — owner-scoped unless caller is org:admin.
     * @returns ServiceAccountListResponse Successful Response
     * @throws ApiError
     */
    public static listServiceAccounts({
        cursor,
        limit = 50,
        status,
    }: {
        cursor?: (string | null),
        limit?: number,
        status?: (string | null),
    }): CancelablePromise<ServiceAccountListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/service-accounts',
            query: {
                'cursor': cursor,
                'limit': limit,
                'status': status,
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
     * Create Service Account
     * Create a new service account.
     * @returns ServiceAccountResponse Successful Response
     * @throws ApiError
     */
    public static createServiceAccount({
        requestBody,
    }: {
        requestBody: ServiceAccountCreateRequest,
    }): CancelablePromise<ServiceAccountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/service-accounts',
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
     * Archive Service Account
     * Archive a service account — terminal-but-kept.
     *
     * The row is retained for history, but the action is not reversible and
     * the account's scope grants are revoked. For the reversible kill switch
     * use ``:disable`` / ``:enable`` instead.
     * @returns void
     * @throws ApiError
     */
    public static archiveServiceAccount({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/service-accounts/{service_account_id}',
            path: {
                'service_account_id': serviceAccountId,
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
     * Get Service Account
     * Get service account by ID — requires service-accounts:read or self-read.
     * @returns ServiceAccountResponse Successful Response
     * @throws ApiError
     */
    public static getServiceAccount({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<ServiceAccountResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/service-accounts/{service_account_id}',
            path: {
                'service_account_id': serviceAccountId,
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
     * Get Service Account Scopes
     * List scopes granted to a service account.
     * @returns ServiceAccountScopesResponse Successful Response
     * @throws ApiError
     */
    public static getServiceAccountScopes({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<ServiceAccountScopesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/service-accounts/{service_account_id}/scopes',
            path: {
                'service_account_id': serviceAccountId,
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
     * Replace Service Account Scopes
     * Replace all scopes for a service account.
     * @returns ServiceAccountScopesResponse Successful Response
     * @throws ApiError
     */
    public static replaceServiceAccountScopes({
        serviceAccountId,
        requestBody,
    }: {
        serviceAccountId: string,
        requestBody: ServiceAccountScopesRequest,
    }): CancelablePromise<ServiceAccountScopesResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/service-accounts/{service_account_id}/scopes',
            path: {
                'service_account_id': serviceAccountId,
            },
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
     * Approve Service Account
     * Approve a pending service account.
     * @returns ServiceAccountResponse Successful Response
     * @throws ApiError
     */
    public static approveServiceAccount({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<ServiceAccountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/service-accounts/{service_account_id}:approve',
            path: {
                'service_account_id': serviceAccountId,
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
     * Deny Service Account
     * Deny a pending service account.
     * @returns ServiceAccountResponse Successful Response
     * @throws ApiError
     */
    public static denyServiceAccount({
        serviceAccountId,
        requestBody,
    }: {
        serviceAccountId: string,
        requestBody: jentic_one__auth__web__schemas__service_accounts__DenyRequest,
    }): CancelablePromise<ServiceAccountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/service-accounts/{service_account_id}:deny',
            path: {
                'service_account_id': serviceAccountId,
            },
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
     * Disable Service Account
     * Disable an active service account.
     * @returns void
     * @throws ApiError
     */
    public static disableServiceAccount({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/service-accounts/{service_account_id}:disable',
            path: {
                'service_account_id': serviceAccountId,
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
     * Enable Service Account
     * Enable a disabled service account.
     * @returns void
     * @throws ApiError
     */
    public static enableServiceAccount({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/service-accounts/{service_account_id}:enable',
            path: {
                'service_account_id': serviceAccountId,
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
     * Generate Service Account Api Key
     * Generate a new API key for a service account. Rotates any existing key.
     * @returns ApiKeyResponse Successful Response
     * @throws ApiError
     */
    public static generateServiceAccountApiKey({
        serviceAccountId,
    }: {
        serviceAccountId: string,
    }): CancelablePromise<ApiKeyResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/service-accounts/{service_account_id}:generate-api-key',
            path: {
                'service_account_id': serviceAccountId,
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
}
