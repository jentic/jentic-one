/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DailyExecutionBucket } from './DailyExecutionBucket';
import type { TopOperation } from './TopOperation';
/**
 * Aggregated execution statistics for the dashboard.
 */
export type ExecutionStatsResponse = {
    /**
     * Per-day execution counts, oldest first; days with no executions are omitted.
     */
    daily_buckets: Array<DailyExecutionBucket>;
    /**
     * Share of executions in the window that completed, as a percentage rounded to two decimals; 0 when there were none.
     */
    success_rate_percent: number;
    /**
     * The most-executed operations in the window, most executions first (up to 10).
     */
    top_operations: Array<TopOperation>;
    /**
     * Number of executions started in the window.
     */
    total_executions: number;
};

