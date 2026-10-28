/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ApiSpecService {
    /**
     * Get Api Spec
     * Download the OpenAPI spec for the API's current (live) revision.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getApiSpec({
        vendor,
        name,
        version,
        overlays = true,
    }: {
        /**
         * Vendor of the API. Normalised to its stored slug (lowercased; each run of characters outside `[a-z0-9-]` becomes `-`; leading and trailing `-` removed), so `Stripe.com` and `stripe-com` are equivalent. Responses and `_links` use the slug form. A value with no slug characters is rejected with 422.
         */
        vendor: string,
        /**
         * Name of the API. Normalised to its stored slug (lowercased; each run of characters outside `[a-z0-9-]` becomes `-`; leading and trailing `-` removed), so `Stripe.com` and `stripe-com` are equivalent. Responses and `_links` use the slug form. A value with no slug characters is rejected with 422.
         */
        name: string,
        /**
         * Version of the API. Matched exactly after trimming surrounding whitespace; it is not slugified, so `1.1.4` and `1-1-4` are different versions.
         */
        version: string,
        /**
         * Reserved for applying overlays at read time; currently has no effect, as confirmed overlays are already part of the stored document.
         */
        overlays?: boolean,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/openapi',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
            },
            query: {
                'overlays': overlays,
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
     * Download an API revision's OpenAPI document
     * Download the stored OpenAPI document of one revision.
     *
     * Choose the format with `Accept`: `application/json` (the default), or
     * `application/openapi+yaml` / `application/yaml`; anything else returns
     * 406. The response carries `Content-Disposition: attachment` with a
     * `.json` or `.yaml` filename. The document is served exactly as stored —
     * a revision produced by confirming an overlay already includes it, so the
     * `overlays` flag currently has no effect.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getApiRevisionSpec({
        vendor,
        name,
        version,
        revisionId,
        overlays = true,
    }: {
        /**
         * Vendor of the API. Normalised to its stored slug (lowercased; each run of characters outside `[a-z0-9-]` becomes `-`; leading and trailing `-` removed), so `Stripe.com` and `stripe-com` are equivalent. Responses and `_links` use the slug form. A value with no slug characters is rejected with 422.
         */
        vendor: string,
        /**
         * Name of the API. Normalised to its stored slug (lowercased; each run of characters outside `[a-z0-9-]` becomes `-`; leading and trailing `-` removed), so `Stripe.com` and `stripe-com` are equivalent. Responses and `_links` use the slug form. A value with no slug characters is rejected with 422.
         */
        name: string,
        /**
         * Version of the API. Matched exactly after trimming surrounding whitespace; it is not slugified, so `1.1.4` and `1-1-4` are different versions.
         */
        version: string,
        /**
         * ID of the API revision.
         */
        revisionId: string,
        /**
         * Reserved for applying overlays at read time; currently has no effect, as confirmed overlays are already part of the stored document.
         */
        overlays?: boolean,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/revisions/{revision_id}/openapi',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'revision_id': revisionId,
            },
            query: {
                'overlays': overlays,
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
