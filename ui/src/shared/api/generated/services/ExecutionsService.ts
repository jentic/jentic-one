/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionListResponse } from '../models/ExecutionListResponse';
import type { ExecutionResponse } from '../models/ExecutionResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ExecutionsService {
    /**
     * List Executions
     * List execution records with optional filters.
     * @returns ExecutionListResponse Successful Response
     * @throws ApiError
     */
    public static listExecutions({
        traceId,
        status,
        from,
        to,
        api,
        actorId,
        origin,
        cursor,
        limit = 25,
    }: {
        /**
         * Only return executions with this trace ID.
         */
        traceId?: (string | null),
        /**
         * Only return executions with one of these terminal statuses (`completed` or `failed`); repeat the parameter for several.
         */
        status?: (Array<string> | null),
        /**
         * Only return executions started at or after this time (RFC 3339).
         */
        from?: (string | null),
        /**
         * Only return executions started before this time (RFC 3339).
         */
        to?: (string | null),
        /**
         * Only return executions of this API, given as `vendor[:name[:version]]`.
         */
        api?: (string | null),
        /**
         * Only return executions run by this actor.
         */
        actorId?: (string | null),
        /**
         * Only return executions initiated from this surface, e.g. `api` or `mcp`.
         */
        origin?: (string | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of execution records to return (1-100, default 25).
         */
        limit?: number,
    }): CancelablePromise<ExecutionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions',
            query: {
                'trace_id': traceId,
                'status': status,
                'from': from,
                'to': to,
                'api': api,
                'actor_id': actorId,
                'origin': origin,
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
     * Get Execution
     * Get an execution record by ID.
     * @returns ExecutionResponse Successful Response
     * @throws ApiError
     */
    public static getExecution({
        executionId,
    }: {
        /**
         * ID of the execution record.
         */
        executionId: string,
    }): CancelablePromise<ExecutionResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions/{execution_id}',
            path: {
                'execution_id': executionId,
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
