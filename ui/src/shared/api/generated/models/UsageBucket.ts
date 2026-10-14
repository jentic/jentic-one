/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Execution counts for a single time bucket.
 */
export type UsageBucket = {
    /**
     * Mean execution duration in the bucket, in milliseconds.
     */
    avg_ms: number;
    /**
     * Number of those executions that failed.
     */
    failed: number;
    /**
     * Number of those executions that completed.
     */
    success: number;
    /**
     * Number of executions started in the bucket.
     */
    total: number;
    /**
     * Start of the bucket, in Unix epoch seconds.
     */
    ts: number;
};

