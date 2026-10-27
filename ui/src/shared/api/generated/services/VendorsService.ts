/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VendorAuthCapabilitiesResponse } from '../models/VendorAuthCapabilitiesResponse';
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
     * rows and the platform-shipped ``vendors`` config. The two never dedupe —
     * each active registration and each config entry is its own picker card,
     * and ``flow_kinds`` lists every flow that card can run. Never returns
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
     * Get a vendor's OAuth flows and scopes
     * Describe how to connect a vendor: its OAuth flows and scope catalogue.
     *
     * `vendor_key` is a key from `GET /vendors`. Returns the vendor's display
     * name, the OAuth `flows` it supports (`authorization_code` and/or
     * `device_authorization`), and every scope with its `classification`
     * (`read`, `write` or `admin`), whether it is requested by default, and a
     * description. Use it to build a connect screen before starting a connect
     * session. When the vendor has several admin-registered OAuth apps, pass
     * `oauth_app_registration_id` to pick one; without it the request fails
     * with 400 (`ambiguous_vendor`). The OAuth client secret is never returned.
     * An unknown vendor returns 404 (`unknown_vendor`).
     * @returns VendorAuthCapabilitiesResponse Successful Response
     * @throws ApiError
     */
    public static getAuthCapabilities({
        vendorKey,
        oauthAppRegistrationId,
    }: {
        /**
         * Vendor registry key.
         */
        vendorKey: string,
        /**
         * Pin to a specific admin-registered OAuth app when the vendor has more than one. Returns that registration's scopes + client_id, so the picker's tile and the connect payload stay aligned.
         */
        oauthAppRegistrationId?: (string | null),
    }): CancelablePromise<VendorAuthCapabilitiesResponse> {
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
