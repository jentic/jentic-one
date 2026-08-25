/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DiscoveryService {
    /**
     * JSON Web Key Set
     * Return the JWKS document with the active public signing keys (ES256).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static jwks(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/jwks.json',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * OAuth authorization server metadata
     * Return RFC 8414 authorization-server metadata (endpoints, grant types, algorithms).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static oauthAuthorizationServer(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-authorization-server',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * External IdP login descriptor
     * Public capability hint: whether IdP login is enabled, and which provider.
     *
     * The SPA reads this before login to decide whether to show a "Continue with
     * <provider>" button. Unauthenticated and secret-free — it advertises only the
     * enabled flag and the provider name (never client secrets or endpoints).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static authIdpDescriptor(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/auth/idp',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
