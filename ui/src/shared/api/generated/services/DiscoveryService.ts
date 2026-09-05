/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CapabilitiesResponse } from '../models/CapabilitiesResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DiscoveryService {
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
    /**
     * Deployment capabilities
     * Return this deployment's public self-description.
     *
     * Unauthenticated and DB-free so any client can discover — from one URL —
     * which sign-in methods this deployment supports, where its broker is,
     * which surfaces are mounted, and which optional features are enabled,
     * instead of probe-and-guess across ``/instance``, ``/auth/idp``, and the
     * RFC 8414 document.
     * @returns CapabilitiesResponse Successful Response
     * @throws ApiError
     */
    public static getCapabilities(): CancelablePromise<CapabilitiesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/capabilities',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
