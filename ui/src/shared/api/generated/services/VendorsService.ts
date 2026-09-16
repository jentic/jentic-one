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
     * Public metadata for every vendor in the config-seeded registry.
     *
     * Used by the UI's "Add integration" picker. Never returns secrets.
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
     * at response build time).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAuthCapabilities({
        vendorKey,
    }: {
        vendorKey: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/vendors/{vendor_key}/auth-capabilities',
            path: {
                'vendor_key': vendorKey,
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
