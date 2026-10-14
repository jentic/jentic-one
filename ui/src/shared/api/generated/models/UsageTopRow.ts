/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Top entity row with sparkline trend.
 */
export type UsageTopRow = {
    /**
     * Mean execution duration for this entity, in milliseconds.
     */
    avg_ms: number;
    /**
     * Number of those executions that failed.
     */
    failed: number;
    /**
     * Grouping key for the row: `vendor/name` for `api`, the toolkit or credential ID, or `actor_type/actor_id` for `agent`; `unknown` when the value is missing.
     */
    key: string;
    /**
     * Display label for the row.
     */
    label: string;
    /**
     * Number of those executions that completed.
     */
    success: number;
    /**
     * Number of executions for this entity in the window.
     */
    total: number;
    /**
     * Execution counts for this entity across equal segments of the window, oldest first.
     */
    trend: Array<number>;
};

