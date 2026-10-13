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
        vendor: string,
        name: string,
        version: string,
        cursor?: (string | null),
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
     * @returns any Successful Response
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
        vendor: string,
        name: string,
        version: string,
        revisionId: string,
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<any> {
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
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
