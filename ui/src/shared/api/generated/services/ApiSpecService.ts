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
        vendor: string,
        name: string,
        version: string,
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
        vendor: string,
        name: string,
        version: string,
        revisionId: string,
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
