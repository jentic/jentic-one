/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiImportResponse } from '../models/ApiImportResponse';
import type { CatalogEntryResponse } from '../models/CatalogEntryResponse';
import type { CatalogListResponse } from '../models/CatalogListResponse';
import type { CatalogRefreshResponse } from '../models/CatalogRefreshResponse';
import type { CatalogSnoozeRequest } from '../models/CatalogSnoozeRequest';
import type { OperationPreviewListResponse } from '../models/OperationPreviewListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CatalogService {
    /**
     * List Catalog
     * List a keyset page of browsable catalog entries (search/filter aware).
     *
     * The catalog holds thousands of entries, so this is cursor-paginated like
     * ``GET /apis``: follow ``next_cursor`` until ``has_more`` is false.
     * ``catalog_total``/``registered_count``/``outdated_count`` count the whole
     * manifest, not the page, so the Discover status row stays stable while scrolling.
     * @returns CatalogListResponse Successful Response
     * @throws ApiError
     */
    public static listCatalog({
        q,
        registeredOnly = false,
        unregisteredOnly = false,
        outdatedOnly = false,
        includeSnoozed = false,
        cursor,
        limit = 50,
    }: {
        q?: (string | null),
        registeredOnly?: boolean,
        unregisteredOnly?: boolean,
        outdatedOnly?: boolean,
        includeSnoozed?: boolean,
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<CatalogListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog',
            query: {
                'q': q,
                'registered_only': registeredOnly,
                'unregistered_only': unregisteredOnly,
                'outdated_only': outdatedOnly,
                'include_snoozed': includeSnoozed,
                'cursor': cursor,
                'limit': limit,
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
     * Get Catalog Entry
     * Retrieve a single catalog entry by api_id.
     * @returns CatalogEntryResponse Successful Response
     * @throws ApiError
     */
    public static getCatalogEntry({
        apiId,
    }: {
        apiId: string,
    }): CancelablePromise<CatalogEntryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{api_id}',
            path: {
                'api_id': apiId,
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
     * Preview Catalog Operations
     * Preview the operations of a catalog entry's spec (capped, offset-paginated).
     *
     * ``tag`` and ``q`` filter the spec's operations server-side before windowing,
     * so the UI's search box covers every operation in the spec and pages the
     * filtered set via ``offset``/``limit`` ("Load more").
     * @returns OperationPreviewListResponse Successful Response
     * @throws ApiError
     */
    public static previewCatalogOperations({
        apiId,
        offset,
        limit = 200,
        tag,
        q,
    }: {
        apiId: string,
        offset?: number,
        limit?: number,
        tag?: (string | null),
        q?: (string | null),
    }): CancelablePromise<OperationPreviewListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{api_id}/operations',
            path: {
                'api_id': apiId,
            },
            query: {
                'offset': offset,
                'limit': limit,
                'tag': tag,
                'q': q,
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
     * Import Catalog Entry
     * Enqueue an async import of a catalog entry into the local registry.
     * @returns ApiImportResponse Successful Response
     * @throws ApiError
     */
    public static importCatalogEntry({
        apiId,
    }: {
        apiId: string,
    }): CancelablePromise<ApiImportResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/{api_id}:import',
            path: {
                'api_id': apiId,
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
     * Snooze Catalog Entry
     * Snooze the outstanding update notification for a catalog entry (C1, #925).
     *
     * Operator action (``events:write``): quiet the "update available" badge for a
     * known-and-accepted upstream change without adopting it. Body is optional — omit or send
     * ``{"snoozed_until": null}`` to mute until a newer upstream digest lands (the primary
     * per-API affordance); send an ISO-8601 ``snoozed_until`` for a time-boxed snooze. A
     * genuinely newer upstream change re-lights the badge automatically.
     * @returns void
     * @throws ApiError
     */
    public static snoozeCatalogEntry({
        apiId,
        requestBody,
    }: {
        apiId: string,
        requestBody?: (CatalogSnoozeRequest | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/{api_id}:snooze',
            path: {
                'api_id': apiId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `The catalog entry has no outstanding update to snooze.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Unsnooze Catalog Entry
     * Clear a snooze for a catalog entry (C1). Operator action (``events:write``).
     * @returns void
     * @throws ApiError
     */
    public static unsnoozeCatalogEntry({
        apiId,
    }: {
        apiId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/{api_id}:unsnooze',
            path: {
                'api_id': apiId,
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
     * Refresh Catalog
     * Force a refresh of the catalog cache from the upstream manifest (org:admin).
     * @returns CatalogRefreshResponse Successful Response
     * @throws ApiError
     */
    public static refreshCatalog(): CancelablePromise<CatalogRefreshResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog:refresh',
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
