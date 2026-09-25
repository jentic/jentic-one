/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthorizationCodeRegistrationCreateRequest } from '../models/AuthorizationCodeRegistrationCreateRequest';
import type { DeviceAuthorizationRegistrationCreateRequest } from '../models/DeviceAuthorizationRegistrationCreateRequest';
import type { OAuthAppRegistrationFlowKind } from '../models/OAuthAppRegistrationFlowKind';
import type { OAuthAppRegistrationListResponse } from '../models/OAuthAppRegistrationListResponse';
import type { OAuthAppRegistrationResponse } from '../models/OAuthAppRegistrationResponse';
import type { OAuthAppRegistrationRotateSecretRequest } from '../models/OAuthAppRegistrationRotateSecretRequest';
import type { OAuthAppRegistrationUpdateRequest } from '../models/OAuthAppRegistrationUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OAuthAppRegistrationsService {
    /**
     * List OAuth app registrations
     * List OAuth application registrations visible to the caller.
     *
     * Any authenticated caller with ``credentials:read`` sees the shared
     * registrations they might connect through; admin-only routes gate on
     * ``org:admin`` separately.
     * @returns OAuthAppRegistrationListResponse Successful Response
     * @throws ApiError
     */
    public static listOauthAppRegistrations({
        apiVendor,
        includeInactive = false,
        flowKind,
    }: {
        /**
         * Filter by vendor slug.
         */
        apiVendor?: (string | null),
        /**
         * Include registrations with is_active=false in the response.
         */
        includeInactive?: boolean,
        /**
         * Filter by OAuth flow kind.
         */
        flowKind?: (OAuthAppRegistrationFlowKind | null),
    }): CancelablePromise<OAuthAppRegistrationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth-app-registrations',
            query: {
                'api_vendor': apiVendor,
                'include_inactive': includeInactive,
                'flow_kind': flowKind,
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
     * Register a shared OAuth application
     * Register a shared OAuth application that users on this instance can SSO through.
     *
     * The client secret is stored encrypted and never returned by any read
     * endpoint — reads only expose ``has_client_secret`` + ``secret_last_rotated_at``.
     * @returns OAuthAppRegistrationResponse Successful Response
     * @throws ApiError
     */
    public static createOauthAppRegistration({
        requestBody,
    }: {
        requestBody: (AuthorizationCodeRegistrationCreateRequest | DeviceAuthorizationRegistrationCreateRequest),
    }): CancelablePromise<OAuthAppRegistrationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth-app-registrations',
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
     * Delete an OAuth app registration
     * Delete a registration.
     *
     * Refused if any credentials still reference it — revoke those first, or
     * deactivate the registration with a PATCH ``is_active=false`` instead.
     * @returns void
     * @throws ApiError
     */
    public static deleteOauthAppRegistration({
        id,
    }: {
        id: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/oauth-app-registrations/{id}',
            path: {
                'id': id,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Conflict`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get an OAuth app registration
     * Get an OAuth app registration by id.
     * @returns OAuthAppRegistrationResponse Successful Response
     * @throws ApiError
     */
    public static getOauthAppRegistration({
        id,
    }: {
        id: string,
    }): CancelablePromise<OAuthAppRegistrationResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth-app-registrations/{id}',
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
     * Update an OAuth app registration
     * Update mutable fields on a registration.
     *
     * ``client_id`` is immutable (would orphan every dependent credential) —
     * use ``:rotate-secret`` for the client secret.
     * @returns OAuthAppRegistrationResponse Successful Response
     * @throws ApiError
     */
    public static updateOauthAppRegistration({
        id,
        requestBody,
    }: {
        id: string,
        requestBody: OAuthAppRegistrationUpdateRequest,
    }): CancelablePromise<OAuthAppRegistrationResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/oauth-app-registrations/{id}',
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
     * Rotate the client secret
     * Replace the encrypted client secret. Existing tokens are unaffected;
     * subsequent refreshes present the new secret.
     * @returns OAuthAppRegistrationResponse Successful Response
     * @throws ApiError
     */
    public static rotateOauthAppRegistrationSecret({
        id,
        requestBody,
    }: {
        id: string,
        requestBody: OAuthAppRegistrationRotateSecretRequest,
    }): CancelablePromise<OAuthAppRegistrationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/oauth-app-registrations/{id}:rotate-secret',
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
