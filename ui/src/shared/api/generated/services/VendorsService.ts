/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VendorListResponse } from '../models/VendorListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class VendorsService {
    /**
     * List verified vendors
     * Public metadata for every vendor known to the platform.
     *
     * Unioned across two sources: admin-registered ``oauth_app_registrations``
     * rows and the platform-shipped ``vendors`` config. When a vendor slug
     * exists in both, the DB row wins so admin-managed registrations always
     * take precedence in the UI's "Add integration" picker. Never returns
     * secrets.
     * @returns VendorListResponse Successful Response
     * @throws ApiError
     */
    public static listVendors(): CancelablePromise<VendorListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/vendors',
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
     * Get a vendor's SSO capabilities
     * Full auth capabilities for one vendor — flows, scopes, classifications.
     *
     * Never returns client_secret (authorization-code flow's secret is stripped
     * at response build time). ``UnknownVendorError`` maps to a 404 problem
     * detail via the handler registered in ``control/web/app.py``.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAuthCapabilities({
        vendorKey,
        oauthAppRegistrationId,
    }: {
        vendorKey: string,
        /**
         * Pin to a specific admin-registered OAuth app when the vendor has more than one. Returns that registration's scopes + client_id, so the picker's tile and the connect payload stay aligned.
         */
        oauthAppRegistrationId?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/vendors/{vendor_key}/auth-capabilities',
            path: {
                'vendor_key': vendorKey,
            },
            query: {
                'oauth_app_registration_id': oauthAppRegistrationId,
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
