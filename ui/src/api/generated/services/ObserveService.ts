/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JobListPage } from '../models/JobListPage';
import type { JobOut } from '../models/JobOut';
import type { TraceListPage } from '../models/TraceListPage';
import type { TraceOut } from '../models/TraceOut';
import type { UsageResponse } from '../models/UsageResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ObserveService {
    /**
     * List async jobs — paginated handles for outstanding and completed async calls
     * Returns async jobs only — calls that could not complete synchronously. Sync calls produce traces but no jobs. Filter by `status` (pending|running|complete|failed|upstream_async). Poll `GET /jobs/{id}` for individual job status.
     * @returns JobListPage Successful Response
     * @throws ApiError
     */
    public static listJobsJobsGet({
        status,
        kind,
        page = 1,
        limit = 20,
        toolkitId,
        agentId,
        since,
        until,
        q,
    }: {
        /**
         * Filter by status. Accepts a single value or a comma-separated set (e.g. `pending,running` for in-flight only). Whitespace tolerated.
         */
        status?: (string | null),
        /**
         * Filter by job kind: `workflow` (multi-step Arazzo runs) or `broker` (individual API calls dispatched async). Used by the Monitor Jobs tab to split workflow runs from broker calls in separate views.
         */
        kind?: (string | null),
        /**
         * Page number (1-indexed)
         */
        page?: number,
        /**
         * Results per page (1-100)
         */
        limit?: number,
        /**
         * Filter by toolkit id (exact match)
         */
        toolkitId?: (string | null),
        /**
         * Filter by agent client_id (exact match).
         */
        agentId?: (string | null),
        /**
         * Lower bound on `created_at` (unix seconds, inclusive)
         */
        since?: (number | null),
        /**
         * Upper bound on `created_at` (unix seconds, exclusive)
         */
        until?: (number | null),
        /**
         * Free-text substring match across slug_or_id, agent_id, toolkit_id, upstream_job_url. Whitespace-only treated as unset.
         */
        q?: (string | null),
    }): CancelablePromise<JobListPage> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/jobs',
            query: {
                'status': status,
                'kind': kind,
                'page': page,
                'limit': limit,
                'toolkit_id': toolkitId,
                'agent_id': agentId,
                'since': since,
                'until': until,
                'q': q,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Poll async job — check status and retrieve result when complete
     * Poll this endpoint after receiving a 202. The job_id comes from the `Location` response header (RFC 7240) or the `X-Jentic-Job-Id` header. Returns `status: pending|running` while in progress. Returns `status: complete` with `result` when done. Returns `status: upstream_async` when the upstream API itself returned 202 — check `upstream_job_url` to follow the upstream job. Returns `status: failed` with `error` and `http_status` on failure.
     * @returns JobOut Successful Response
     * @throws ApiError
     */
    public static getJobRouteJobsJobIdGet({
        jobId,
    }: {
        /**
         * Job ID (format: job_{12chars})
         */
        jobId: string,
    }): CancelablePromise<JobOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/jobs/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel async job — best-effort cancellation of an outstanding job
     * Requests cancellation of a pending or running async job. Best-effort: cancellation fires at the next async checkpoint; an in-flight upstream HTTP request will complete before the job stops. The job record is retained (marked failed, error='Cancelled by client'). Has no effect on already-completed jobs.
     * @returns void
     * @throws ApiError
     */
    public static cancelJobJobsJobIdDelete({
        jobId,
    }: {
        /**
         * Job ID to cancel
         */
        jobId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/jobs/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List execution traces — audit recent broker and workflow calls
     * Returns recent execution traces with status, capability id, toolkit, timestamp, and HTTP status. Use GET /traces/{trace_id} for step-level detail.
     * @returns TraceListPage Successful Response
     * @throws ApiError
     */
    public static listTracesTracesGet({
        limit = 20,
        offset,
        toolkitId,
        agentId,
        apiId,
        status,
        since,
        until,
        capabilityId,
        q,
    }: {
        /**
         * Maximum number of traces to return (1-500)
         */
        limit?: number,
        /**
         * Number of traces to skip for pagination
         */
        offset?: number,
        /**
         * Filter by toolkit id (exact match)
         */
        toolkitId?: (string | null),
        /**
         * Filter by agent client_id (exact match). Admin-only signal.
         */
        agentId?: (string | null),
        /**
         * Filter by upstream API. Exact match against the `api_id` column on executions, which is the catalog-form `apis.id` (e.g. `stripe.com`, `github.com`). Indexed; use this in preference to scanning `operation_id` substrings.
         */
        apiId?: (string | null),
        /**
         * Filter by trace status (`success` | `failed` | `pending`)
         */
        status?: (string | null),
        /**
         * Lower bound on `created_at` (unix seconds, inclusive)
         */
        since?: (number | null),
        /**
         * Upper bound on `created_at` (unix seconds, exclusive)
         */
        until?: (number | null),
        /**
         * Filter by exact capability id. Matches `operation_id` for broker calls or `workflow_id` for workflow runs.
         */
        capabilityId?: (string | null),
        /**
         * Free-text substring match across operation_id, workflow_id, api_id, agent_id. Whitespace-only treated as unset.
         */
        q?: (string | null),
    }): CancelablePromise<TraceListPage> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/traces',
            query: {
                'limit': limit,
                'offset': offset,
                'toolkit_id': toolkitId,
                'agent_id': agentId,
                'api_id': apiId,
                'status': status,
                'since': since,
                'until': until,
                'capability_id': capabilityId,
                'q': q,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Trace usage aggregations — bucketed counts and top groups
     * Aggregate execution traces in a time window for monitoring dashboards.
     *
     * The endpoint serves three pieces of information in one round-trip:
     *
     * 1. `stats` — totals, success/failed split, mean and p50/p95 latency, and a
     * point-in-time count of in-flight async jobs. Powers the HealthStrip.
     * 2. `buckets` — equally-sized time slices for stacking success/failed bar
     * charts. Bucket width is chosen by the server based on the window:
     * windows ≤ 1h use 60s buckets, ≤ 24h use 1h buckets, anything bigger
     * uses 1d buckets. We never return more than ~144 buckets.
     * 3. `top` — the top N groups (toolkits, agents or API hosts) by trace count.
     *
     * All filters compose with AND semantics on top of the tenant scope.
     * @returns UsageResponse Successful Response
     * @throws ApiError
     */
    public static getUsageTracesUsageGet({
        since,
        until,
        groupBy = 'toolkit',
        topLimit = 10,
        toolkitId,
        agentId,
        apiId,
        status,
    }: {
        /**
         * Window start (unix seconds, inclusive). Defaults to 24h ago.
         */
        since?: (number | null),
        /**
         * Window end (unix seconds, exclusive). Defaults to now.
         */
        until?: (number | null),
        /**
         * What to group the `top` list by: 'toolkit' | 'api' | 'agent'.
         */
        groupBy?: string,
        /**
         * Maximum rows in `top` list (1–50)
         */
        topLimit?: number,
        /**
         * Filter to one toolkit before aggregating
         */
        toolkitId?: (string | null),
        /**
         * Filter to one agent before aggregating
         */
        agentId?: (string | null),
        /**
         * Filter by upstream API. Exact match against the indexed `api_id` column on executions (catalog-form `apis.id`, e.g. `stripe.com`). Same semantics as `/traces?api_id=`.
         */
        apiId?: (string | null),
        /**
         * Filter to a single status before aggregating
         */
        status?: (string | null),
    }): CancelablePromise<UsageResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/traces/usage',
            query: {
                'since': since,
                'until': until,
                'group_by': groupBy,
                'top_limit': topLimit,
                'toolkit_id': toolkitId,
                'agent_id': agentId,
                'api_id': apiId,
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get trace detail — step-by-step execution log
     * Returns the full execution trace with all steps: capability called, inputs, outputs, HTTP status, and timing. Useful for debugging failed workflow steps.
     * @returns TraceOut Successful Response
     * @throws ApiError
     */
    public static getTraceTracesTraceIdGet({
        traceId,
    }: {
        /**
         * Trace ID (format: exec_{12chars})
         */
        traceId: string,
    }): CancelablePromise<TraceOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/traces/{trace_id}',
            path: {
                'trace_id': traceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
