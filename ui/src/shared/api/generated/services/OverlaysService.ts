/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OverlayConfirmRequest } from '../models/OverlayConfirmRequest';
import type { OverlaySubmitRequest } from '../models/OverlaySubmitRequest';
import type { OverlayUpdateRequest } from '../models/OverlayUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OverlaysService {
    /**
     * List Overlays
     * List overlays for an API with optional status filter and cursor pagination.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listOverlays({
        vendor,
        name,
        version,
        cursor,
        limit = 50,
        status,
    }: {
        vendor: string,
        name: string,
        version: string,
        cursor?: (string | null),
        limit?: number,
        status?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/overlays',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
            },
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
     * Submit Overlay
     * Submit a new overlay for an API.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static submitOverlay({
        vendor,
        name,
        version,
        requestBody,
    }: {
        vendor: string,
        name: string,
        version: string,
        requestBody: OverlaySubmitRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/overlays',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
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
     * Deprecate Overlay
     * Deprecate an overlay (soft delete).
     * @returns void
     * @throws ApiError
     */
    public static deprecateOverlay({
        vendor,
        name,
        version,
        overlayId,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Get Overlay
     * Retrieve a single overlay by ID.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getOverlay({
        vendor,
        name,
        version,
        overlayId,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Update Overlay
     * Update an overlay's document or target revision.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static updateOverlay({
        vendor,
        name,
        version,
        overlayId,
        requestBody,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
        requestBody: OverlayUpdateRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Confirm Overlay
     * Confirm an overlay, materializing it onto the served spec.
     *
     * Requires ``org:admin``: confirming rewrites the API's served spec (it re-ingests
     * the base spec with the overlay applied and promotes the result to the current
     * revision), so it is an operator action, not a contributor one (contributors
     * ``submit`` overlays with ``apis:write``; an operator reviews and confirms).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static confirmOverlay({
        vendor,
        name,
        version,
        overlayId,
        requestBody,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
        requestBody: OverlayConfirmRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}:confirm',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
}
