/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Overall usage statistics for a time window.
 */
export type UsageStatsBlock = {
    /**
     * Number of asynchronous executions a worker is running right now, regardless of the window. Queued executions not yet picked up by a worker are not counted. The same filters apply as for `pending`.
     */
    active_now: number;
    /**
     * Mean execution duration in milliseconds; 0 when there were no executions.
     */
    avg_ms: number;
    /**
     * Number of those executions that failed.
     */
    failed: number;
    /**
     * Median execution duration in milliseconds; null when not available.
     */
    p50_ms: (number | null);
    /**
     * 95th-percentile execution duration in milliseconds; null when not available.
     */
    p95_ms: (number | null);
    /**
     * Number of asynchronous executions queued in the window that have not finished yet (still queued or running). They are not part of `total`, which counts finished executions only. A synchronous execution is counted once it finishes, never here. The `api_id` and `agent_id` filters apply; a `status` filter makes this 0, since no unfinished execution has a final status.
     */
    pending: number;
    /**
     * Number of those executions that completed.
     */
    success: number;
    /**
     * Number of executions started in the window.
     */
    total: number;
};

