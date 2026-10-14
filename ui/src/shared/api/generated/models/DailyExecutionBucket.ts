/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Execution counts for a single day.
 */
export type DailyExecutionBucket = {
    /**
     * The day the bucket covers, as `YYYY-MM-DD`.
     */
    date: string;
    /**
     * Number of those executions that failed.
     */
    failed: number;
    /**
     * Number of those executions that completed.
     */
    success: number;
    /**
     * Number of executions started that day.
     */
    total: number;
};

