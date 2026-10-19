/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionStatsResponse } from '../models/ExecutionStatsResponse';
import type { GroupBy } from '../models/GroupBy';
import type { UsageResponse } from '../models/UsageResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MonitoringService {
    /**
     * Get Execution Stats
     * Return aggregated execution statistics for the dashboard.
     * @returns ExecutionStatsResponse Successful Response
     * @throws ApiError
     */
    public static getExecutionStats({
        days = 7,
    }: {
        /**
         * Number of days back from now to aggregate over (1-30, default 7).
         */
        days?: number,
    }): CancelablePromise<ExecutionStatsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/monitoring/executions',
            query: {
                'days': days,
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
     * Get Usage Stats
     * Return detailed usage statistics for the monitoring overview.
     * @returns UsageResponse Successful Response
     * @throws ApiError
     */
    public static getUsageStats({
        since,
        until,
        groupBy,
        topLimit = 10,
        apiId,
        agentId,
        status,
    }: {
        /**
         * Start of the window in Unix epoch seconds (inclusive); defaults to 24 hours before `until`.
         */
        since?: (number | null),
        /**
         * End of the window in Unix epoch seconds (exclusive); defaults to the next minute boundary after now.
         */
        until?: (number | null),
        /**
         * Dimension to group the `top` rows by; defaults to `api`.
         */
        groupBy?: (GroupBy | null),
        /**
         * Maximum number of `top` rows to return (1-50, default 10).
         */
        topLimit?: number,
        /**
         * Only count executions of this API, given as `vendor/name`.
         */
        apiId?: (string | null),
        /**
         * Only count executions run by this actor ID.
         */
        agentId?: (string | null),
        /**
         * Only count executions with this status, e.g. `completed` or `failed`.
         */
        status?: (string | null),
    }): CancelablePromise<UsageResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/monitoring/usage',
            query: {
                'since': since,
                'until': until,
                'group_by': groupBy,
                'top_limit': topLimit,
                'api_id': apiId,
                'agent_id': agentId,
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
}
