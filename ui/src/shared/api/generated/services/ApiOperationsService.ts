/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationSummaryListResponse } from '../models/OperationSummaryListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ApiOperationsService {
    /**
     * List the operations an API currently serves
     * List every operation in the API's current (live) revision.
     *
     * Each row gives the operation's id, method, path, name, description, tags,
     * `deprecated` flag, and an `inspect` link for its full detail. Rows come in
     * a stable, unranked order and are cursor-paginated (`cursor`, `limit`).
     * Use this to browse an API exhaustively; to find operations by intent,
     * use `GET /search` instead. 404 (`no_current_revision`) when the API has no live
     * revision.
     * @returns OperationSummaryListResponse Successful Response
     * @throws ApiError
     */
    public static listApiOperations({
        vendor,
        name,
        version,
        cursor,
        limit = 50,
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
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of operations to return (1-200).
         */
        limit?: number,
    }): CancelablePromise<OperationSummaryListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/operations',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
            },
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `The API does not exist (type \`api_not_found\`), or it exists but has no live revision to list operations from (type \`no_current_revision\`).`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List the operations in an API revision
     * List every operation in one specific revision, live or not.
     *
     * Same rows, ordering and cursor pagination as the live-revision listing,
     * but for the named revision, so you can review a draft before promoting
     * it or compare against an archived one. Follow each row's `inspect` link
     * for full detail. 404 if the API or revision does not exist.
     * @returns OperationSummaryListResponse Successful Response
     * @throws ApiError
     */
    public static listApiRevisionOperations({
        vendor,
        name,
        version,
        revisionId,
        cursor,
        limit = 50,
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
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of operations to return (1-200).
         */
        limit?: number,
    }): CancelablePromise<OperationSummaryListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/revisions/{revision_id}/operations',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'revision_id': revisionId,
            },
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `The API (type \`api_not_found\`) or the revision (type \`revision_not_found\`) does not exist.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
