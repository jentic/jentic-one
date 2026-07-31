/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiImportResponse } from '../models/ApiImportResponse';
import type { CatalogEntryResponse } from '../models/CatalogEntryResponse';
import type { CatalogListResponse } from '../models/CatalogListResponse';
import type { CatalogRefreshResponse } from '../models/CatalogRefreshResponse';
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
        cursor,
        limit = 50,
    }: {
        q?: (string | null),
        registeredOnly?: boolean,
        unregisteredOnly?: boolean,
        outdatedOnly?: boolean,
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
