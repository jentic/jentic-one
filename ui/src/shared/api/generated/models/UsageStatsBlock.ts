/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Overall usage statistics for a time window.
 */
export type UsageStatsBlock = {
    /**
     * Reserved count of in-flight executions; currently always 0.
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
     * Reserved count of pending executions; currently always 0.
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

